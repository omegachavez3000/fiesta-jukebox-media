import json
import os
from flask import Flask, request, jsonify, Response, stream_with_context, send_from_directory
from flask_cors import CORS
import yt_dlp
import requests

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ---------------------------------------------------------
# ESTADO GLOBAL Y CACHÉ DE STREAMING (Cero retraso de carga)
# ---------------------------------------------------------
PLAYLIST_QUEUE = []
CURRENT_TRACK = None
STREAM_CACHE = {}  # Caché para almacenar URLs directas y evitar re-ejecutar yt-dlp

YTDL_OPTIONS = {
    'format': 'bestaudio[ext=m4a]/bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
    }
}

# Sirve el archivo index.html en la raíz
@app.route('/')
def serve_index():
    return send_from_directory('.', 'index.html')

# ---------------------------------------------------------
# ENDPOINTS DE LA COLA Y ESTADO
# ---------------------------------------------------------
@app.route('/api/queue/select', methods=['POST'])
def select_from_queue():
    """Selecciona un tema de la cola por su índice y lo pone a sonar."""
    global CURRENT_TRACK, PLAYLIST_QUEUE
    data = request.json or {}
    index = data.get('index')
    
    if index is not None and 0 <= index < len(PLAYLIST_QUEUE):
        CURRENT_TRACK = PLAYLIST_QUEUE.pop(index)
        
    return jsonify({'current_track': CURRENT_TRACK, 'queue': PLAYLIST_QUEUE})

@app.route('/api/state', methods=['GET'])
def get_state():
    """Obtiene el estado actual (canción reproducida + cola)."""
    return jsonify({
        'current_track': CURRENT_TRACK,
        'queue': PLAYLIST_QUEUE
    })

@app.route('/api/queue', methods=['GET'])
def get_queue():
    """Obtiene únicamente la lista de reproducción."""
    return jsonify(PLAYLIST_QUEUE)

@app.route('/api/queue', methods=['POST'])
def add_to_queue():
    """Agrega una nueva canción enviada desde la PC o celular."""
    data = request.json or {}
    if not data.get('id'):
        return jsonify({'error': 'Track ID is required'}), 400

    track = {
        'id': data.get('id'),
        'title': data.get('title', 'Sin título'),
        'artist': data.get('artist', 'Artista desconocido'),
        'duration': data.get('duration', 0),
        'thumbnail': data.get('thumbnail', '')
    }
    
    PLAYLIST_QUEUE.append(track)
    return jsonify({'success': True, 'queue': PLAYLIST_QUEUE})

@app.route('/api/queue/next', methods=['POST'])
def next_track():
    """Avanza a la siguiente canción en la cola."""
    global CURRENT_TRACK
    if PLAYLIST_QUEUE:
        CURRENT_TRACK = PLAYLIST_QUEUE.pop(0)
    else:
        CURRENT_TRACK = None
    return jsonify({'current_track': CURRENT_TRACK, 'queue': PLAYLIST_QUEUE})

@app.route('/api/queue/delete', methods=['POST'])
def delete_from_queue():
    """Elimina un tema específico de la cola por su ID."""
    data = request.json or {}
    track_id = data.get('id')
    
    global PLAYLIST_QUEUE
    PLAYLIST_QUEUE = [t for t in PLAYLIST_QUEUE if t.get('id') != track_id]
    return jsonify({'success': True, 'queue': PLAYLIST_QUEUE})

# ---------------------------------------------------------
# BÚSQUEDA Y STREAMING OPTIMIZADO CON CACHÉ
# ---------------------------------------------------------

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
        # Usar caché si ya tenemos la URL directa del stream para este video_id
        if video_id in STREAM_CACHE:
            stream_url = STREAM_CACHE[video_id]
        else:
            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
                info = ydl.extract_info(video_url, download=False)
                stream_url = info.get('url')
                STREAM_CACHE[video_id] = stream_url
            
        headers = {**YTDL_OPTIONS['http_headers']}
        if 'Range' in request.headers:
            headers['Range'] = request.headers['Range']

        req = requests.get(stream_url, headers=headers, stream=True)
        
        # Si la URL cacheada expiró (error 403), la borramos del caché y reintentamos una vez
        if req.status_code == 403:
            with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
                info = ydl.extract_info(video_url, download=False)
                stream_url = info.get('url')
                STREAM_CACHE[video_id] = stream_url
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
        # Limpiar caché en caso de error crítico
        if video_id in STREAM_CACHE:
            del STREAM_CACHE[video_id]
        return str(e), 500

if __name__ == '__main__':
    print("🚀 Servidor Party Jukebox Sincronizado y Optimizado en http://0.0.0.0:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
