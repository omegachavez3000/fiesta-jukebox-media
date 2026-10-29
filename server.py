import json
import os
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory
from flask_cors import CORS
import yt_dlp
import requests

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app, resources={r"/api/*": {"origins": "*"}})

YTDL_OPTIONS = {
    'format': 'bestaudio[ext=m4a]/bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
}
# NUEVA RUTA: Sirve el archivo index.html en la raíz del sitio web
@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')
@app.route('/api/search', methods=['GET'])
def search_songs():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])

    search_query = f"ytsearch6:{query}"
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'extract_flat': 'in_playlist', 'http_headers': YTDL_OPTIONS['http_headers']}) as ydl:
            info = ydl.extract_info(search_query, download=False)
            results = []
            for entry in info.get('entries', []):
                results.append({
                    'id': entry.get('id'),
                    'title': entry.get('title'),
                    'artist': entry.get('uploader', 'Artista desconocido'),
                    'duration': entry.get('duration', 0),
                    'thumbnail': entry.get('thumbnails', [{}])[-1].get('url', ''),
                })
            return jsonify(results)
    except Exception as e:
        print(f"Error en la búsqueda: {e}")
        return jsonify([]), 500

@app.route('/api/stream', methods=['GET'])
def stream_audio():
    video_id = request.args.get('id', '')
    if not video_id:
        return "No ID provided", 400

    video_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            info = ydl.extract_info(video_url, download=False)
            stream_url = info.get('url')
            
        # Reenviar headers de rango si el navegador solicita adelantar/retroceder
        headers = {**YTDL_OPTIONS['http_headers']}
        if 'Range' in request.headers:
            headers['Range'] = request.headers['Range']

        req = requests.get(stream_url, headers=headers, stream=True)
        
        response_headers = {
            'Content-Type': req.headers.get('content-type', 'audio/m4a'),
            'Accept-Ranges': 'bytes',
            'Access-Control-Allow-Origin': '*'
        }

        if 'content-length' in req.headers:
            response_headers['Content-Length'] = req.headers['content-length']
        if 'content-range' in req.headers:
            response_headers['Content-Range'] = req.headers['content-range']

        def generate():
            for chunk in req.iter_content(chunk_size=16384):
                if chunk:
                    yield chunk

        status_code = req.status_code if req.status_code in [200, 206] else 200
        return Response(stream_with_context(generate()), status=status_code, headers=response_headers)

    except Exception as e:
        print(f"Error haciendo streaming: {e}")
        return str(e), 500

if __name__ == '__main__':
    print("🚀 Servidor DJ Pro (Seek + Crossfade) en http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
