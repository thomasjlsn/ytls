#!/usr/bin/env python3
"""Get the latest N uploads from a list of channels."""

import atexit
import pickle
import re
import readline
from argparse import ArgumentParser
from os import get_terminal_size, getenv, isatty, makedirs, path, popen, system
from pprint import pprint
from shlex import quote as shellescape
from sys import stderr, stdout
from time import sleep, strftime

BROWSER = getenv('BROWSER', default='firefox')
HOME = getenv('HOME')
XDG_CACHE_HOME = getenv('XDG_CACHE_HOME', default=path.join(HOME, '.cache'))
XDG_DOWNLOADS_DIR = getenv('XDG_DOWNLOADS_DIR', default=path.join(HOME, 'Downloads'))

argparser = ArgumentParser()

argparser.add_argument('--debug', dest='debug', action='store_true',
                       help='enable debug output')

argparser.add_argument('-c', '--color', dest='color', action='store_true',
                       help='(default) colored output')

argparser.add_argument('-C', '--no-color', dest='nocolor', action='store_true',
                       help='non-colored output')

argparser.add_argument('-d', '--download', dest='download', action='store_true',
                       help='download video of choice with `youtube-dl`')

argparser.add_argument('-H', '--hide', dest='hide', action='store_true',
                       help='hide previously opened videos')

argparser.add_argument('-i', '--interactive', dest='interactive', action='store_true',
                       help=f'interactive mode')

args = argparser.parse_args()

DEBUG = False or args.debug
COLOR = (args.color or True) and not args.nocolor
CACHE = path.join(XDG_CACHE_HOME, 'youtube_api_v3')
HIDE = False or args.hide
ISATTY = isatty(0)

if not path.isdir(CACHE):
    makedirs(CACHE)


def clear_line():
    stdout.write('\r\033[2K')


color_codes = {
    'debug':        ('31;1', '31;1'),
    'timestamp':    ('2',    '2'),
    'url':          ('34;4', '35;4'),
    'channel':      ('1',    '2'),
    'index':        ('1',    '2'),
    'text':         ('0',    '0'),
}


def colored(viewed=False, color_key=None, string=''):
    if COLOR and color_key:
        if viewed:
            return f'\033[{color_codes[color_key][1]}m{string}\033[0m'
        else:
            return f'\033[{color_codes[color_key][0]}m{string}\033[0m'
    return string


def debug(string):
    if DEBUG:
        stderr.write(f'\n[{colored(viewed=False, color_key="debug", string="DEBUG")}] {string}\n')


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

    channel_cache = dict()
    channel_cache_path = path.join(CACHE, 'channel_cache.pkl')

    def get_id_of_channel(self, username):
        if path.isfile(self.channel_cache_path):
            with open(self.channel_cache_path, 'rb') as cache:
                debug('loading id cache...')
                try:
                    self.channel_cache = pickle.load(cache)
                except EOFError:  # corrupt / empty cache
                    self.channel_cache = None

            try:
                ID = self.channel_cache.get(username, None)
                channel_id = ID[0]
                uploads_id = ID[1]
            except (NameError, TypeError):
                ID = None

            get_channel_id = True if ID is None else False
        else:
            get_channel_id = True

        if get_channel_id:
            query = self.lazy().channels().list(
                part='contentDetails',
                forUsername=username,
            )

            if COLOR:
                clear_line()
                stdout.write(f'\rfetching channel id for {username}...')

            response = query.execute()
            sleep(self.rate_limit)

            items = response['items']
            channel_id = items[-1]['id']
            uploads_id = items[0]['contentDetails']['relatedPlaylists']['uploads']

            self.channel_cache[username] = (channel_id, uploads_id)

            with open(self.channel_cache_path, 'wb') as cache:
                debug('cached ids')
                pickle.dump(self.channel_cache, cache)

        debug(f'{username} = {self.channel_cache[username]}')

        return self.channel_cache[username]

    def get_latest_uploads_from_channel(self, username=None,
                                        channel_id=None,
                                        uploads_id=None):

        if None in (username, channel_id, uploads_id):
            raise Exception('missing expected kwargs')

        # cache uploads lasting for four hours
        timestamp = ''.join((
            strftime('%Y%m%d'), str(int(strftime('%H')) // 4)
        ))

        uploads_cache_path = path.join(
            CACHE, f'{username}_upload_cache.{timestamp}.pkl'
        )

        uploads = None

        if path.isfile(uploads_cache_path):
            with open(uploads_cache_path, 'rb') as cache:
                try:
                    uploads = pickle.load(cache)
                    get_uploads = False
                except EOFError:
                    get_uploads = True
        else:
            get_uploads = True

        if get_uploads or uploads is None:
            query = self.lazy().playlistItems().list(
                part='contentDetails, snippet',
                playlistId=uploads_id,
                maxResults=25,
            )

            if COLOR:
                clear_line()
                stdout.write(f'\rfetching videos from {username}...')

            response = query.execute()
            sleep(self.rate_limit)
            uploads = response['items']

            with open(uploads_cache_path, 'wb') as cache:
                pickle.dump(uploads, cache)

        return uploads


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
        self.viewed = self.id in viewed_videos

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
            system(' '.join(['1>&2 2>/dev/null', BROWSER, '&']))

        new_tab_cmd = {
            'firefox': 'firefox -new-tab %s',
        }

        self.message('opening')
        system(new_tab_cmd.get(BROWSER, '#') % shellescape(self.url))

    def mark_as_watched(self):
        self.message('marked as watched')

    def list(self, index, search_string=None):

        if self.viewed and HIDE:
            return

        if search_string is not None:
            results = re.search(search_string, self.title, flags=re.IGNORECASE)
            if not results:
                return

        index_column_width = (len(str(len(VIDEOS))))

        max_title_len = (cols - ((index_column_width + 1)
                                 + (len(self.pubdate) + 1)
                                 + (len(self.pubtime) + 1)
                                 + (len(self.channel) + 2)
                                 + (len(self.url) + 2)))

        # debug(f'cols={cols}, title={len(self.title)} max={max_title_len}')

        writer = lambda color_key, string: stdout.write(colored(
            viewed=self.viewed,
            color_key=color_key,
            string=string,
        ))

        if (len(self.title) > max_title_len):
            title = f' {self.title[:(max_title_len)]}… '
        elif COLOR:
            title = f' {self.title}' + (' ' * (max_title_len - (len(self.title) - 1))) + ' '
        else:
            title = f' {self.title} '

        try:
            if search_string is not None:
                results = re.search(search_string, title, flags=re.IGNORECASE)
                matched_separator = results.group(0)
                parts = re.split(matched_separator, title, 1)
                title = f'{parts[0]}\033[31;1m{matched_separator}\033[0m{parts[1]}'
        except AttributeError:
            title = title

        if COLOR:
            clear_line()

        writer('index', f'{str(index).ljust(index_column_width)} ')
        writer('timestamp', ' '.join([self.pubdate, self.pubtime]))
        writer('channel',   f' {self.channel}:')
        writer('text', title)
        writer('url', f'{self.url}\n')


def get_videos(youtube_api, subscriptions, max_vids_displayed_per_channel):
    for user in subscriptions:
        if user.startswith('UC') and len(user) == 24:
            channel_id, uploads_id = (user, re.sub('^UC', 'UU', user))
        else:
            channel_id, uploads_id = youtube_api.get_id_of_channel(user)

        uploads = youtube_api.get_latest_uploads_from_channel(
            username=user,
            channel_id=channel_id,
            uploads_id=uploads_id,
        )

        count = 0

        for item in uploads:
            if count == max_vids_displayed_per_channel:
                break
            count += 1

            yield item['snippet']


def list_videos(videos, *args, **kwargs):
    for index, video in enumerate(VIDEOS):
        Actions(video).list(index, *args, **kwargs)


if __name__ == '__main__':
    with open('subscriptions.txt', 'r') as subs:
        my_subscriptions = (
            line.strip().split(' #')[0].strip()
            for line in subs.readlines()
            if not line.strip().startswith('#')
        )

    cols, _ = get_terminal_size(0)
    # max_vids_displayed_per_channel = 5
    # max_vids_requested_per_channel = 25
    api = YouTubeAPI()

    view_cache = path.join(CACHE, 'viewed_videos.pkl')

    def cache_views():
        with open(view_cache, 'wb') as cache:
            debug('cached viewed_videos')
            pickle.dump(viewed_videos, cache)

    atexit.register(cache_views)

    viewed_videos = set()

    if path.isfile(view_cache):
        with open(view_cache, 'rb') as cache:
            try:
                viewed_videos = pickle.load(cache)
            except EOFError:
                viewed_videos = set()

    VIDEOS = list(get_videos(api, my_subscriptions, 5))

    if not args.interactive:
        list_videos(VIDEOS)
        exit()

    RUN = True

    while RUN:
        while 1:
            try:
                choice = input(f'\033[1mYTLS $\033[0m ')
            except (EOFError, KeyboardInterrupt):
                RUN = False
                break

            if choice in ('?', 'help'):
                stdout.write('''
SHORT   LONG         DESCRIPTION
==============================================================================
?       help         display this message

a N F   audio N F    rip audio from video N (optionally; in format F (mp3 by default))
d N     download N   download video N
o N     open N       open video N in $BROWSER
w N     watched N    mark video N as watched

h       hide         hide watched videos
u       unhide       unhide watched videos

f       fetch        fetch latest videos from YouTube
l       list         list videos
g RE    grep RE      filter videos with regex RE

q       quit         quit

''')
                continue

            if choice == '':
                continue

            if choice in ('q', 'quit'):
                RUN = False
                break

            if choice in ('h', 'hide'):
                HIDE = True
                break

            if choice in ('u', 'unhide'):
                HIDE = False
                break

            if choice in ('l', 'ls', 'list'):
                list_videos(VIDEOS)
                continue

            if choice.startswith(('g ', 'grep ')):
                _, _, pattern = choice.partition(' ')
                list_videos(VIDEOS, search_string=pattern)

            # ==============================================================
            # Doing stuff to / with videos
            # ==============================================================

            if re.match(r'^((d(l|own(load)?))|(o(pen)?)|(w(atched)?))(\s[0-9]+)+$', choice):
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
                    viewed_videos.add(id)
                    Actions(VIDEOS[c]).open_in_browser()

                elif action.startswith('w'):
                    viewed_videos.add(id)
                    Actions(VIDEOS[c]).mark_as_watched()
                    continue

                else:
                    raise Exception
