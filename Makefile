all:
	:

clean:
	rm $(HOME)/.cache/youtube_api_v3/UC* || :

rm-cached-video-ids:
	rm $(HOME)/.cache/youtube_api_v3/channel_ids.pkl || :
