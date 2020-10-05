#!/usr/bin/env python3
"""Get the latest N uploads from a list of channels."""

import atexit
import pickle
import re
import readline
import string
from os import get_terminal_size, getenv, makedirs, path, popen, system
from pprint import pprint
from shlex import quote as shellescape
from sys import stderr, stdout
from time import sleep, strftime

BROWSER = getenv('BROWSER', default='firefox')
HOME = getenv('HOME')
XDG_CACHE_HOME = getenv('XDG_CACHE_HOME', default=path.join(HOME, '.cache'))
XDG_DOWNLOADS_DIR = getenv('XDG_DOWNLOADS_DIR', default=path.join(HOME, 'Downloads'))


class Settings():
    VIDS_DISPLAYED_PER_CHANNEL = 5
    VIDS_REQUESTED_PER_CHANNEL = 50
    DEBUG = False
    HIDE = False


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
    cache_base = path.join(XDG_CACHE_HOME, 'youtube_api_v3')

    if not path.isdir(cache_base):
        makedirs(cache_base)

    def load_cache(self, cache_name, default=None):
        cache_path = path.join(self.cache_base, cache_name)
        cache_data = None

        if path.isfile(cache_path):
            with open(cache_path, 'rb') as cache:
                try:
                    cache_data = pickle.load(cache)
                except EOFError:
                    cache_data = None

        if cache_data is None:
            return default
        return cache_data

    def save_cache(self, cache_name, data=None):
        cache_path = path.join(self.cache_base, cache_name)

        if data is not None:
            with open(cache_path, 'wb') as cache:
                pickle.dump(data, cache)


class LazyLoader:
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


class YouTubeAPI(LazyLoader):
    '''
    Methods querying the YouTube API.
    '''
    max_duty_cycle = (1 / 3)
    rate_limit = ((((24 * max_duty_cycle) * 60) * 60) / 10000)


class ChannelIDs(YouTubeAPI, Cachable):
    '''
    Get id of a channel
    '''
    def __init__(self):
        self.cache_name = 'channel_ids.pkl'

    def get(self, username):
        ids = self.load_cache(self.cache_name, default=dict())
        channel_id = ids.get(username, None)

        if channel_id is None:
            query = self.lazy().channels().list(
                part='contentDetails',
                forUsername=username,
            )

            clear_line()
            stdout.write(f'\rfetching channel id for {username}...')

            response = query.execute()
            sleep(self.rate_limit)

            channel_id = response['items'][-1]['id']
            ids[username] = channel_id

            self.save_cache(self.cache_name, data=ids)

        return channel_id


class ChannelUploads(YouTubeAPI, Cachable):
    '''
    Get uploads from a channel
    '''
    def __init__(self, username=None, channel_id=None):
        self.channel_id = channel_id
        self.uploads_id = re.sub('^UC', 'UU', self.channel_id)
        self.username = username

        # cache uploads lasting for four hours
        self.timestamp = ''.join((
            strftime('%Y%m%d'), str(int(strftime('%H')) // 4)
        ))

        self.cache_name = path.join(
            f'{self.username}_upload_cache.{self.timestamp}.pkl'
        )

    def get(self, force=False):
        uploads = self.load_cache(self.cache_name, default=list())

        if not uploads or force:
            query = self.lazy().playlistItems().list(
                part='contentDetails, snippet',
                playlistId=self.uploads_id,
                maxResults=25,
            )

            clear_line()
            stdout.write(f'\rfetching videos from {self.username}...')

            response = query.execute()
            sleep(self.rate_limit)

            uploads = response['items']
            self.save_cache(self.cache_name, data=uploads)

        return uploads


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
    Doing stuff to individual videos.
    '''
    def __init__(self, video):
        self.video = video
        self.id = self.video['resourceId']['videoId']
        self.url = f'https://youtube.com/watch?v={self.id}'
        self.channel = self.video['channelTitle']
        self.title = self.video["title"]
        self.pubdate = self.video['publishedAt'][2:10]
        self.pubtime = self.video['publishedAt'][11:16]
        self.viewed = self.id in VIEWS.get()

    def message(self, _action):
        stdout.write(' '.join([
            f'{_action}:',
            colored(
                viewed=False,
                color_key="channel",
                string=f'{self.channel}:',
            ),
            f'{self.title}\n',
        ]))

    def download(self):
        self.message('downloading')
        system(' '.join([
            f'cd {shellescape(XDG_DOWNLOADS_DIR)}',
            f'&& youtube-dl {shellescape(self.url)}',
        ]))

    def rip_audio(self, audio_format):
        audio_formats = ['aac', 'flac', 'mp3', 'm4a', 'opus', 'vorbis', 'wav']

        if audio_format not in audio_formats:
            audio_format = 'mp3'

        self.message(f'ripping {audio_format}')
        system(' '.join([
            f'cd {shellescape(XDG_DOWNLOADS_DIR)}',
            f'&& youtube-dl --extract-audio --audio-format {audio_format}',
            shellescape(self.url),
        ]))

    def open_in_browser(self):
        browser_pid = (popen(' '.join(['pidof', shellescape(BROWSER)]))
                       .read().strip().split()) or None

        if browser_pid is None:
            system(' '.join(['1>&2 2>/dev/null exec', BROWSER, '&']))

        new_tab_cmd = {
            'firefox': 'firefox -new-tab %s',
        }

        self.message('opening')
        system(new_tab_cmd.get(BROWSER, '#') % shellescape(self.url))
        self.mark_as_watched()

    def mark_as_watched(self):
        VIEWS.add(self.id)
        self.message('marked as watched')

    def list(self, index, search_string=None):
        if self.viewed and SETTINGS.HIDE:
            return

        title = self.title
        title = re.sub(f'[^{string.printable}]', '_', title)

        if search_string is not None:
            results = re.search(search_string, title, flags=re.IGNORECASE)
            if not results:
                return

        index_column_width = (len(str(len(VIDEOS))))

        max_title_len = (cols - ((index_column_width + 1)
                                 + (len(self.pubdate) + 1)
                                 + (len(self.pubtime) + 1)
                                 + (len(self.channel) + 2)
                                 + (len(self.url) + 2)))

        # debug(f'cols={cols}, title={len(self.title)} max={max_title_len}')

        def writer(color_key, string):
            stdout.write(colored(
                viewed=self.viewed,
                color_key=color_key,
                string=string,
            ))

        if (len(self.title) > max_title_len):
            title = f' {title[:(max_title_len)]}… '
        else:
            title = f' {title}' + (' ' * (max_title_len - (len(self.title) - 1))) + ' '

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
        writer('timestamp', ' '.join([self.pubdate, self.pubtime]))
        writer('channel',   f' {self.channel}:')
        writer('text', title)
        writer('url', f'{self.url}\n')


def sort_by_date(videos):
    return sorted(
        videos, key=lambda v: int(re.sub('[^0-9]', '', v['publishedAt']))
    )


def sort_by_user(videos):
    return sorted(
        videos, key=lambda v: v['channelTitle']
    )


def get_videos(subscriptions, force=False):
    for user in subscriptions:
        if user.startswith('UC') and len(user) == 24:
            channel_id = user
        else:
            channel_id = SUBSCRIPTIONS.get(user)

        uploads = ChannelUploads(
            username=user,
            channel_id=channel_id,
        ).get(force=force)

        count = 0

        for item in uploads:
            if count == SETTINGS.VIDS_DISPLAYED_PER_CHANNEL:
                break
            count += 1

            yield item['snippet']


def list_videos(videos, **kwargs):
    for index, video in enumerate(VIDEOS):
        Actions(video).list(index, **kwargs)


def get_subscriptions():
    with open('subscriptions.txt', 'r') as subs:
        return (
            line.strip().split(' #')[0].strip()
            for line in subs.readlines()
            if not line.strip().startswith('#')
        )




if __name__ == '__main__':
    SETTINGS = Settings()
    SUBSCRIPTIONS = ChannelIDs()
    VIEWS = ViewHistory()
    VIDEOS = list(get_videos(get_subscriptions()))

    while True:
        cols, _ = get_terminal_size(0)
        try:
            clear_line()
            choice = input(f'\033[1mYTLS $\033[0m ')
        except (EOFError, KeyboardInterrupt):
            break

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
h       hide         hide watched videos
H       unhide       unhide watched videos
d       date         sort by upload date
u       user         sort by username
g RE    grep RE      filter videos with regex RE


''')
            continue

        if choice == '':
            continue

        if choice in ('q', 'quit'):
            break

        if choice in ('h', 'hide'):
            SETTINGS.HIDE = True
            continue

        if choice in ('H', 'unhide'):
            SETTINGS.HIDE = False
            continue

        if choice in ('l', 'ls', 'list'):
            list_videos(VIDEOS)
            continue

        if choice.startswith(('g', 'grep')):
            _, _, pattern = choice.partition(' ')
            list_videos(VIDEOS, search_string=pattern)
            continue

        if choice.startswith(('n', 'number')):
            _, _, num_videos = choice.partition(' ')

            if num_videos == '':
                print(SETTINGS.VIDS_DISPLAYED_PER_CHANNEL)
                continue

            SETTINGS.VIDS_DISPLAYED_PER_CHANNEL = min(
                SETTINGS.VIDS_REQUESTED_PER_CHANNEL, int(num_videos)
            )
            continue

        if choice.startswith(('f ', 'fetch ')) and choice.endswith(('f', 'force')):
            VIDEOS = list(get_videos(get_subscriptions(), force=True))
            list_videos(VIDEOS)
            continue

        if choice in ('f', 'fetch'):
            VIDEOS = list(get_videos(get_subscriptions()))
            list_videos(VIDEOS)
            continue


        if choice in ('u', 'user'):
            VIDEOS = sort_by_user(VIDEOS)
            list_videos(VIDEOS)
            continue

        if choice in ('d', 'date'):
            VIDEOS = sort_by_date(VIDEOS)
            list_videos(VIDEOS)
            continue

        # ==============================================================
        # Doing stuff to / with videos
        # ==============================================================

        if re.match(r'^(d(l|own(load)?)|(o(pen)?)|(w(atched)?))(\s[0-9]+)+$', choice):
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
                continue

            else:
                raise Exception
