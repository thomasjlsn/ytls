#!/usr/bin/env python3
"""Get the latest N uploads from a list of channels."""

import os
import pickle
import re
import readline
import string
from pprint import pprint
from shlex import quote as shellescape
from shutil import which
from sys import stderr, stdout
from time import sleep, strftime

BROWSER = os.getenv('BROWSER', default='firefox')
HOME = os.getenv('HOME')
XDG_CACHE_HOME = os.getenv('XDG_CACHE_HOME', default=os.path.join(HOME, '.cache'))
XDG_DOWNLOADS_DIR = os.getenv('XDG_DOWNLOADS_DIR', default=os.path.join(HOME, 'Downloads'))


class Settings():
    VIDS_REQUESTED_PER_CHANNEL = 50
    DEBUG = False
    HIDE = False
    KEYWORDS = set()
    SHOW_URL = False


def clear_line():
    stdout.write('\r\033[2K')


color_codes = {
    'debug':        ('31;1', '31;1'),
    'timestamp':    ('2',    '2'),
    'url':          ('34;4', '35;4;2'),
    'channel':      ('1',    '1'),
    'index':        ('1',    '2'),
    'text':         ('0',    '0'),
}


def colored(viewed=False, color_key=None, string=''):
    if color_key:
        if viewed:
            return f'\033[{color_codes[color_key][1]}m{string}\033[0m'
        else:
            return f'\033[{color_codes[color_key][0]}m{string}\033[0m'
    return string


def debug(string):
    if SETTINGS.DEBUG:
        stderr.write(f'\n[{colored(viewed=False, color_key="debug", string="DEBUG")}] {string}\n')


class Cachable:
    '''
    Cache data on disk for faster lookups / persistent session state
    '''
    cache_base = os.path.join(XDG_CACHE_HOME, 'youtube_api_v3')

    if not os.path.isdir(cache_base):
        os.makedirs(cache_base)

    def load_cache(self, cache_name, default=None):
        cache_path = os.path.join(self.cache_base, cache_name)
        cache_data = None

        if os.path.isfile(cache_path):
            with open(cache_path, 'rb') as cache:
                try:
                    cache_data = pickle.load(cache)
                except EOFError:
                    cache_data = None

        if cache_data is None:
            return default
        return cache_data

    def save_cache(self, cache_name, data=None):
        cache_path = os.path.join(self.cache_base, cache_name)

        if data is not None:
            with open(cache_path, 'wb') as cache:
                pickle.dump(data, cache)


class LazyLoaded:
    '''
    The Google API lib is slooooooooooow to import,
    this class lazily loads it with the lazy() method.
    '''
    _api = None

    def lazy(self):
        if self._api is None:
            with open('API_KEY', 'r') as key:
                from googleapiclient.discovery import build
                self._api = build(
                    'youtube', 'v3', developerKey=key.read().strip()
                )

        return self._api


class YouTubeAPI(Cachable, LazyLoaded):
    '''
    Methods querying the YouTube API.
    '''
    max_duty_cycle = (1 / 3)
    rate_limit = ((((24 * max_duty_cycle) * 60) * 60) / 10000)


class ChannelID(YouTubeAPI):
    '''
    Get id of a channel.
    '''
    def __init__(self):
        self.cache_name = 'channel_ids.pkl'

    def get(self, username):
        ids = self.load_cache(self.cache_name, default=dict())
        channel_id = ids.get(username, None)

        clear_line()
        stdout.write(f'\rfetching channel id for "{username}"...')

        if channel_id is None:
            query = self.lazy().channels().list(
                part='contentDetails',
                forUsername=username,
            )

            response = query.execute()

            channel_id = response['items'][-1]['id']
            ids[username] = channel_id

            self.save_cache(self.cache_name, data=ids)

        return channel_id


class ChannelUploads(YouTubeAPI):
    '''
    Get uploads from a channel.
    '''
    def __init__(self, username=None, channel_id=None, timestamp=''):
        self.channel_id = channel_id
        self.uploads_id = re.sub('^UC', 'UU', self.channel_id)
        self.username = username

        self.cache_name = os.path.join(
            f'{self.channel_id}.{timestamp.ljust(10, "0")}.pkl'
        )

    def get(self, force=False):
        uploads = self.load_cache(self.cache_name, default=list())

        clear_line()
        stdout.write(f'\rfetching videos from "{self.username}"...')

        if not uploads or force:
            query = self.lazy().playlistItems().list(
                part='contentDetails, snippet',
                playlistId=self.uploads_id,
                maxResults=SETTINGS.VIDS_REQUESTED_PER_CHANNEL,
            )

            response = query.execute()
            # sleep(self.rate_limit)

            uploads = response['items']
            self.save_cache(self.cache_name, data=uploads)

        return uploads


class VideoDetails(YouTubeAPI):
    '''
    Get information about a specific video.
    '''
    def __init__(self, video_id=None, timestamp=''):
        if type(video_id) == 'list':
            self.video_id = ', '.join(video_id)
        else:
            self.video_id = video_id

        self.cache_name = os.path.join(
            f'{self.video_id}.{timestamp.ljust(10, "0")}.pkl'
        )

    def get(self, force=False):
        stats = self.load_cache(self.cache_name, default=list())

        if not stats or force:
            query = self.lazy().videos().list(
                part='statistics, contentDetails, snippet',
                id=self.video_id,
            )

            response = query.execute()
            # sleep(self.rate_limit)

            stats = response['items']
            self.save_cache(self.cache_name, data=stats)

        return stats


class ViewHistory(Cachable):
    '''
    Keep track of which videos have been viewed.
    '''
    def __init__(self):
        self.cache_name = 'view_history.pkl'
        self.views = set()

    def add(self, video_id):
        self.views.add(video_id)
        self.save_cache(self.cache_name, data=self.views)

    def get(self):
        self.views = self.load_cache(self.cache_name, default=set())
        return self.views


class Actions:
    '''
    Doing stuff to video objects.
    '''
    def __init__(self, video):
        self.video = video

    def message(self, _action):
        stdout.write(' '.join([
            f'{_action}:',
            colored(
                viewed=False,
                color_key="channel",
                string=f'{self.video.channel}:',
            ),
            f'{self.video.title}\n',
        ]))

    def download(self):
        if which('youtube-dl'):
            self.message('downloading')
            os.system(' '.join([
                f'cd {shellescape(XDG_DOWNLOADS_DIR)}',
                f'&& youtube-dl {shellescape(self.video.url)}',
            ]))

    def rip_audio(self, audio_format):
        if which('youtube-dl'):
            audio_formats = ['aac', 'flac', 'mp3', 'm4a', 'opus', 'vorbis', 'wav']

            if audio_format not in audio_formats:
                audio_format = 'mp3'

            self.message(f'ripping {audio_format}')
            os.system(' '.join([
                f'cd {shellescape(XDG_DOWNLOADS_DIR)}',
                f'&& youtube-dl --extract-audio --audio-format {audio_format}',
                shellescape(self.video.url),
            ]))

    def open_in_browser(self):
        if os.name == 'posix':
            if which('pidof'):
                def browser_pid():
                    return [
                        p for p in
                        os.popen(' '.join(['pidof', shellescape(BROWSER)]))
                        .read().strip().split()
                    ] or None

                if browser_pid() is None:
                    os.system(f'1>&2 2>/dev/null nohup {BROWSER}&')

                new_tab_cmd = {
                    'firefox': 'firefox -new-tab %s',
                }

                while browser_pid() is None:
                    sleep(0.1)

                self.message('opening')
                os.system(new_tab_cmd.get(BROWSER, '#') % shellescape(self.video.url))
                self.mark_as_watched()
        else:
            stderr.write(f'not yet implemented for os type: {os.name}\n')

    def mark_as_watched(self):
        VIEWS.add(self.video.id)
        self.message('marked as watched')

    def list(self, index, search_string=None):
        if self.video.viewed and SETTINGS.HIDE:
            return

        title = self.video.title
        title = re.sub(f'[^{string.printable}]', '_', title)

        if search_string is not None:
            results = re.search(search_string, title, flags=re.IGNORECASE)
            if not results:
                return

        index_column_width = (len(str(len(VIDEOS))))

        max_title_len = (cols - ((index_column_width + 1)
                                 + ((len(self.video.pubdate) + 1))
                                 + ((len(self.video.pubtime) + 1))
                                 + ((len(self.video.channel) + 2))
                                 + ((len(self.video.url) * SETTINGS.SHOW_URL) + 2)
                                 ))

        debug(f'cols={cols}, title={len(self.video.title)} max={max_title_len}')

        def writer(color_key, string):
            stdout.write(colored(
                viewed=self.video.viewed,
                color_key=color_key,
                string=string,
            ))

        if max_title_len < 6:
            print('screen not wide enough')
            return
        elif (len(self.video.title) > max_title_len):
            title = f' {title[:(max_title_len)]}… '
        else:
            title = f' {title}' + (' ' * (max_title_len - (len(self.video.title) - 1))) + ' '

        try:
            if search_string is not None:
                results = re.search(search_string, title, flags=re.IGNORECASE)
                matched_separator = results.group(0)
                parts = re.split(matched_separator, title, 1)
                title = f'{parts[0]}\033[31;1m{matched_separator}\033[0m{parts[1]}'
        except AttributeError:
            title = title

        clear_line()
        writer('index', f'{str(index).ljust(index_column_width)} ')
        writer('timestamp', ' '.join([self.video.pubdate, self.video.pubtime]))
        writer('channel',   f' {self.video.channel}:')
        writer('text', title)
        if SETTINGS.SHOW_URL:
            writer('url', f'{self.video.url}\n')
        else:
            stdout.write('\n')


class Video:
    def __init__(self, video):
        self.id = video['resourceId']['videoId']
        self.url = f'https://youtube.com/watch?v={self.id}'
        self.channel = video['channelTitle']
        self.title = video["title"]
        self.pubdate = video['publishedAt'][2:10]
        self.pubtime = video['publishedAt'][11:16]
        self.viewed = self.id in VIEWS.get()

        clear_line()
        stdout.write(f'\rfetching details of video "{self.title}"...')
        self._details = VideoDetails(video_id=self.id).get(force=False)

        self.description = self._details[0]['snippet'].get('description', None)
        self.comments = int(self._details[0]['statistics'].get('commentCount', 0))
        self.dislikes = int(self._details[0]['statistics'].get('dislikeCount', 0))
        self.likes = int(self._details[0]['statistics'].get('likeCount', 0))
        self.views = int(self._details[0]['statistics'].get('viewCount', 0))

        # pprint(self._details)
        # exit(7)


class Sorted:
    '''
    Sort operations for Video objects
    '''
    def __init__(self, videos):
        self.value = videos

    def get(self):
        return self.value

    @property
    def by_date(self):
        return Sorted(sorted(
            self.value,
            key=lambda v: int(re.sub(r'[^0-9]', '', ''.join((v.pubdate, v.pubtime))))
        ))

    @property
    def by_user(self):
        return Sorted(sorted(
            self.value,
            key=lambda v: v.channel
        ))

    @property
    def by_views(self):
        return Sorted(sorted(
            self.value,
            key=lambda v: v.views
        ))

    @property
    def reversed(self):
        return Sorted(list(reversed(self.value)))


def get_videos(subscriptions, force=False):
    for subscription in subscriptions:
        cl, num, user = subscription

        if user.startswith('UC') and len(user) == 24:
            channel_id = user
        else:
            channel_id = SUBSCRIPTIONS.get(user)

        timestamp = {
            'h': strftime('%Y%m%d%H'),
            'd': strftime('%Y%m%d'),
            'w': strftime('%Y%m%U'),
            'm': strftime('%Y%m'),
            'y': strftime('%Y'),
        }.get(
            cl, ''.join((strftime('%Y%m%d'), str(int(strftime('%H')) // 4)))
        )

        uploads = ChannelUploads(
            username=user,
            channel_id=channel_id,
            timestamp=timestamp,
        ).get(force=force)

        count = 0

        for item in uploads:
            if count == int(num):
                break
            count += 1

            video = Video(item['snippet'])
            # video = item['snippet']
            # print(video)

            yield video


def list_videos(videos, **kwargs):
    for index, video in enumerate(VIDEOS):
        Actions(video).list(index, **kwargs)


def parse_config_file():
    sublist = []
    with open('subscriptions.conf', 'r') as subs:
        for line in (l.strip() for l in subs.readlines()):
            if line.startswith('#'):
                continue

            if re.match(rf'^[{string.whitespace}]*$', line):
                continue

            if re.match(r'^[hdmw]\t[0-9]+\t.*$', line):
                if '#' in line:
                    line = re.sub('#.*$', '', line).strip()
                line = line.split('\t')
                sublist.append(line)
                continue

            if re.match(r'^[ a-zA-Z0-9()|\\?\[\]{}^$._-]+$', line):
                SETTINGS.KEYWORDS.add(line.strip())
                continue

            # print(line)
        # exit()

    return sublist


if __name__ == '__main__':
    SETTINGS = Settings()
    SUBSCRIPTIONS = ChannelID()
    VIEWS = ViewHistory()
    VIDEOS = list(get_videos(parse_config_file()))

    # Actions(VIDEOS[0]).get_video_details()

    while True:
        try:
            clear_line()
            choice = input(f'\033[1mYTLS $\033[0m ')
        except (EOFError, KeyboardInterrupt):
            break

        cols, _ = os.get_terminal_size(0)

        if choice in ('?', 'help'):
            stdout.write('''
CATAGORY
==============================================================================
SHORT   LONG         DESCRIPTION


GENERAL
==============================================================================
?       help         display this message
q       quit         quit
f       fetch        fetch latest videos from YouTube (or from local cache)
n N     number N     when fetching, display N videos (5 by default)


VIDEOS
==============================================================================
a N F   audio N F    rip audio from video N (optionally; in format F
                     (mp3 by default))
dl N    download N   download video N
o N     open N       open video N in $BROWSER
w N     watched N    mark video N as watched


LISTING
==============================================================================
l       list         list videos
d       date         sort by upload date
c       channel      sort by channel name
h       hide         hide watched videos
H       nohide       show watched videos
u       url          show url
U       nourl        hide url
g RE    grep RE      filter videos with regex RE


''')
            continue

        if choice == '':
            continue

        if choice in ('q', 'quit'):
            break

        if choice in ('h', 'hide'):
            SETTINGS.HIDE = True
            list_videos(VIDEOS)
            continue

        if choice in ('H', 'nohide'):
            SETTINGS.HIDE = False
            list_videos(VIDEOS)
            continue

        if choice in ('u', 'url'):
            SETTINGS.SHOW_URL = True
            list_videos(VIDEOS)
            continue

        if choice in ('U', 'nourl'):
            SETTINGS.SHOW_URL = False
            list_videos(VIDEOS)
            continue

        if choice in ('l', 'ls', 'list'):
            list_videos(VIDEOS)
            continue

        if choice.startswith(('g', 'grep')):
            _, _, pattern = choice.partition(' ')
            list_videos(VIDEOS, search_string=pattern)
            continue

        if choice in ('k', 'keywords'):
            list_videos(VIDEOS, search_string=f'({"|".join(SETTINGS.KEYWORDS)})')
            continue

        if choice.startswith(('f ', 'fetch ')) and choice.endswith(('f', 'force')):
            VIDEOS = list(get_videos(parse_config_file(), force=True))
            list_videos(VIDEOS)
            continue

        if choice in ('f', 'fetch'):
            VIDEOS = list(get_videos(parse_config_file()))
            list_videos(VIDEOS)
            continue

        if choice in ('c', 'channel'):
            VIDEOS = (Sorted(VIDEOS)
                      .by_user
                      .get())

            list_videos(VIDEOS)
            continue

        if choice in ('d', 'date'):
            VIDEOS = (Sorted(VIDEOS)
                      .by_date
                      .get())
            list_videos(VIDEOS)
            continue

        if choice in ('v', 'views'):
            VIDEOS = (Sorted(VIDEOS)
                      .by_views
                      .get())

            list_videos(VIDEOS)
            continue

        # Example of chaining sorts TODO
        # if choice == 'my new sort':
        #     VIDEOS = (Sorted(VIDEOS)
        #               .by_user
        #               .by_date
        #               .reversed
        #               .get())
        #     list_videos(VIDEOS)
        #     continue

        # ==============================================================
        # Doing stuff to / with videos
        # ==============================================================

        if re.match(r'^(d(l|own(load)?)|(o(pen)?)|(w(atched)?))(\s[0-9]+)+\s*$', choice):
            action, *choice = choice.split()

        elif re.match(r'^a(udio)?(\s[0-9]+)+$', choice):
            action, *choice = choice.split()
            audio_format = 'mp3'

        elif re.match(r'^a(udio)?(\s[0-9]+)+\s[a-zA-Z0-9]+$', choice):
            action, *choice, audio_format = choice.split()

        else:
            continue

        choice = [int(c) for c in choice]

        for c in choice:
            if c not in range(0, len(VIDEOS)):
                continue

            if action.startswith('a'):
                Actions(VIDEOS[c]).rip_audio(audio_format)

            elif action.startswith('d'):
                Actions(VIDEOS[c]).download()

            elif action.startswith('o'):
                Actions(VIDEOS[c]).open_in_browser()

            elif action.startswith('w'):
                Actions(VIDEOS[c]).mark_as_watched()

            else:
                raise Exception
