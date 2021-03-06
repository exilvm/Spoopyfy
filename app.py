from spotipy import Spotify
from youtubesearchpython import VideosSearch
from pytube import YouTube
from pydub import AudioSegment
import sys
from os import remove, path
from os import getenv as env
import requests
from io import BytesIO
from dotenv import load_dotenv
from tinydb import TinyDB, Query
import math
from eyed3 import load as eyed3_loader
from eyed3.id3 import ID3_V2_3
from urllib.request import urlopen
from spotipy.oauth2 import SpotifyClientCredentials
import json
import logging
from slugify import slugify
from copy import deepcopy

load_dotenv()

folder_path = env('FOLDER_PATH')
download_path = path.join(path.dirname(__file__), folder_path)
spotify_client_id = env('SPOTIFY_CLIENT_ID')
spotify_client_secret = env('SPOTIFY_CLIENT_SECRET')
spotify_playlist_url = env('SPOTIFY_PLAYLIST_URL')
tracklist_path = path.join(env('FOLDER_PATH'), '.tracklist.json')
log_path = path.join(env('FOLDER_PATH'), '.log')

spotify_instance = Spotify(client_credentials_manager=SpotifyClientCredentials(client_id=spotify_client_id, client_secret=spotify_client_secret))

logging.basicConfig(filename=log_path, level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def track_filename(t,):
    return slugify(t['artists'][0]['name'] + ' - ' + t['name'], entities=False, decimal=False, hexadecimal=False, max_length=0, word_boundary=False, separator='_', save_order=False, stopwords=(), regex_pattern=None, lowercase=True, replacements=())

def hhmmss_to_seconds(t):
    l = list(map(int, t.split(':')))
    return sum(n * sec for n, sec in zip(l[::-1], (1, 60, 3600)))

def cleanup(result, state):
    for i in result['fail_list']:
        if i in state['new_list']:
            state['new_list'].remove(i)
    for i in state['removed']:
        try:
            remove(path.join(download_path, track_filename(i) + '.mp3'))
        except Exception as e:
            logging.error('Could not delete the song from the directory.', exc_info=True)
    try:
        with open(tracklist_path, 'w') as file:
            json.dump(state['new_list'], file)
    except Exception as e:
        logging.error('Could not update the local playlist database.', exc_info=True)

def playlist_diff(list1, list2):
    diff = []
    for i in list1:
        if i not in list2:
            diff.append(i)
    return diff

def add_audio_meta(track_info, track_path, comment):
    try:
        t = track_info
        audio_file = eyed3_loader(track_path)
        if not audio_file.tag:
            audio_file.initTag()
        tag=audio_file.tag
        tag.artist = t['artists'][0]['name']
        tag.album = t['album']['name']
        tag.title = t['name']
        tag.album_artist = t['album']['artists'][0]['name']
        tag.recording_date = t['album']['release_date'][0:4]
        tag.images.set(3, urlopen(t['album']['images'][0]['url']).read() , 'image/jpeg', u'cover')
        tag.comments.set(comment)
        tag.save(version=ID3_V2_3)
    except Exception as e:
        logging.error(e, exc_info=True)

def playlist_deconstruct(spotify_playlist_url):
    state = {
        'spotify_response': None,
        'old_list': [],
        'new_list': [],
        'added': [],
        'removed': []
    }
    try:
        state['spotify_response'] = spotify_instance.playlist_tracks(spotify_playlist_url)
        if not state['spotify_response']['items']:
            logging.info('Cannot download from an empty playlist.')
            return
        playlist_chunk = deepcopy(state['spotify_response'])
        while playlist_chunk['next'] is not None:
            next_chunk = spotify_instance.next(playlist_chunk)
            state['spotify_response']['items'].extend(next_chunk['items'])
            playlist_chunk = next_chunk
            next = playlist_chunk['next']
    except Exception as e:
        logging.error(e, exc_info=True)
        return
    for i in state['spotify_response']['items']:
        state['new_list'].append(i['track'])
    try:
        with open(tracklist_path, 'r') as file:
            state['old_list'] = json.load(file)
    except Exception as e:
        logging.error(e, exc_info=True)
    state['added'] = playlist_diff(state['new_list'], state['old_list'])
    state['removed'] = playlist_diff(state['old_list'], state['new_list'])
    return state

def yt_lookup(track):
    t = track
    search_query = t['artists'][0]['name'] + ' - ' + t['name'] + ' Audio'
    search_result = None
    logging.info(f'Looking up {search_query} on Youtube...')
    try:
        search_result = VideosSearch(search_query, limit = 8).result()['result']
        if not search_result:
            logging.info('Could not find any results on Youtube.')
    except Exception as e:
        logging.error(f'Could not lookup videos on Youtube.', exc_info=True)
        return
    for i in search_result:
        duration_comparison = 'close'
        video_duration = hhmmss_to_seconds(i['duration'])
        song_duration = t['duration_ms'] / 1000
        duration_difference = abs(video_duration - song_duration)
        def if_close(abs_tol):
            return math.isclose(video_duration, song_duration, abs_tol=abs_tol)
        logging.info('Testing if {}: http://youtu.be/{} is an accurate result...'.format(i['title'], i['id']))
        if t['artists'][0]['name'].lower() not in i['title'].lower():
            logging.info('Skipped a result item! The video title does not contain the name of the artist, so it is probably not an accurate result.')
            continue
        if t['name'].lower() not in i['title'].lower():
            logging.info('The video title does not contain the name of the song, so it is probably not an accurate result.')
            continue
        if not if_close(90):
            logging.info(f'Skipped a result item! The video and song durations are not close enough. The Spotify song is {song_duration} seconds, while the Youtube video is: {video_duration} seconds, with a difference of {duration_difference} seconds.')
            continue
        if if_close(4):
            duration_comparison = 'super close'
            logging.info(f'Found a {duration_comparison} result! The video and song durations are super close. The Spotify song is {song_duration} seconds, while the Youtube video is: {video_duration} seconds, with a difference of {duration_difference} seconds.')
        else:
            logging.info(f'Found a {duration_comparison} result! The video and song durations are super close. The Spotify song is {song_duration} seconds, while the Youtube video is: {video_duration} seconds, with a difference of {duration_difference} seconds.')
        logging.info('Found a {} result. {}: http://youtu.be/{}'.format(duration_comparison, i['title'], i['id']))
        return {
            'id': i['id'], 'comment': f'Duration comparison between the downloaded song and the original: {duration_comparison} (Duration difference in seconds: {duration_difference} seconds). Downloaded from Youtube at: http://youtu.be/{i["id"]} ({i["title"]})'
        }

def playlist_dl(state):
    result = {
        'success_list': [],
        'fail_list': []
    }
    for i in state['added']:
        song_name = i['artists'][0]['name'] + ' - ' + i['name']
        logging.info(f'Downloading: {song_name}...')
        audio_buffer = BytesIO()
        yt_res = yt_lookup(i)
        file_path = path.join(download_path, track_filename(i) + '.mp3')
        if yt_res == None:
            logging.info('Could not find any results from Youtube.')
            result['fail_list'].append(i)
            continue
        video_id = yt_res['id']
        comment = yt_res['comment']
        try:
            audio = YouTube(f'http://youtu.be/{video_id}').streams.get_audio_only()
            audio.stream_to_buffer(audio_buffer)
            logging.info('Retrieved the audio stream as a buffer.')
        except Exception as e:
            logging.error('Could not download the Youtube stream.', exc_info=True)
            result['fail_list'].append(i)
            continue
        try:
            AudioSegment.from_file(BytesIO(audio_buffer.getvalue())).export(file_path, format='mp3', bitrate='128k')
            add_audio_meta(i, file_path, comment)
            result['success_list'].append(i)
            logging.info('Exported the audio buffer as a file.')
        except Exception as e:
            logging.error('Could not export the buffer as a file', exc_info=True)
            result['fail_list'].append(i)
            continue
    cleanup(result, state)

if __name__ == '__main__':
    track_list = playlist_deconstruct(spotify_playlist_url)
    if track_list is not None:
        playlist_dl(track_list)
