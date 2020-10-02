#!/usr/bin/env python3
"""Get the latest N uploads from a list of channels."""

import pickle
from argparse import ArgumentParser
from os import get_terminal_size, getenv, isatty, makedirs, path
from pprint import pprint
from re import sub
from sys import stderr, stdout
from time import sleep, strftime

# Argument handling.
argparser = ArgumentParser()

argparser.add_argument('-c', '--color',
                       dest='color',
                       action='store_true')

argparser.add_argument('-d', '--debug',
                       dest='debug',
                       action='store_true')

args = argparser.parse_args()

DEBUG = False or args.debug
HOME = getenv('HOME')
XDG_CACHE_HOME = path.join(HOME, '.cache')
CACHE = path.join(XDG_CACHE_HOME, 'youtube_api_v3')
ISATTY = isatty(0)

if not path.isdir(CACHE):
    makedirs(CACHE)


def clear_line():
    stdout.write('\r\033[2K')


color_codes = {
    'debug':     '31;1',
    'timestamp': '2',
    'url':       '34;4',
    'channel':   '1'
}


def colored(color_key, string):
    if args.color:
        return f'\033[{color_codes[color_key]}m{string}\033[0m'
    return string


def debug(string):
    if DEBUG:
        stderr.write(f'\n[{colored("debug", "DEBUG")}] {string}\n')


class LazyLoader:
    # The Google API lib must be huge, it takes ~2-3 seconds to import,
    # this class lazily loads it with the lazy() method.

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

            if args.color:
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

            if args.color:
                clear_line()
                stdout.write(f'\rfetching videos from {username}...')

            response = query.execute()
            sleep(self.rate_limit)
            uploads = response['items']

            with open(uploads_cache_path, 'wb') as cache:
                pickle.dump(uploads, cache)

        return uploads


if __name__ == '__main__':
    with open('subscriptions.txt', 'r') as subs:
        my_subscriptions = (
            line.split('  #')[0].strip()  # allow for comments in subscriptions.txt
            for line in subs.readlines()
        )

    cols, _ = get_terminal_size(0)
    max_vids_displayed_per_channel = 5
    max_vids_requested_per_channel = 25
    api = YouTubeAPI()
    videos = dict()

    for user in my_subscriptions:
        if user.startswith('UC') and len(user) == 24:
            channel_id, uploads_id = (user, sub('^UC', 'UU', user))
        else:
            channel_id, uploads_id = api.get_id_of_channel(user)

        _uploads = api.get_latest_uploads_from_channel(
            username=user,
            channel_id=channel_id,
            uploads_id=uploads_id,
        )

        count = 0

        for item in _uploads:
            if count == max_vids_displayed_per_channel:
                break
            count += 1

            vid = item['snippet']
            id = vid['resourceId']['videoId']
            videos[id] = vid

    for vid_id, vid in videos.items():
        url = f'https://m.youtube.com/watch?v={vid_id}'
        channel = vid['channelTitle']
        title = vid["title"]
        pubdate = vid['publishedAt'][2:10]
        pubtime = vid['publishedAt'][11:16]

        # pprint(vid)
        # break

        if args.color:
            clear_line()

        stdout.write(' '.join([
            f'{colored("timestamp", pubdate)}',
            f'{colored("timestamp", pubtime)}',
            f'{colored("channel", channel)}:',
        ]))

        max_title_len = (cols - ((len(url) + 2)
                                 + (len(channel) + 2)
                                 + (len(pubdate) + 1)
                                 + (len(pubtime) + 1)))

        debug(f'cols={cols}, title={len(title)} max={max_title_len}')

        if args.color and (len(title) > max_title_len):
            stdout.write(f' {title[:(max_title_len)]}…')
        else:
            stdout.write(f' {title}')

        stdout.write(f' {colored("url", url)}\n')
