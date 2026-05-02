@echo off
C:/ffmpeg/bin/ffmpeg.exe -i rtmp://localhost:1935/camera -c:v copy -c:a copy -f flv rtmp://a.rtmp.youtube.com/live2/%YOUTUBE_STREAM_KEY%
