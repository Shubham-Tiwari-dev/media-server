# -*- coding: utf-8 -*-
from flask import Flask, request, redirect, url_for, render_template, Response, jsonify, session, flash
import os, json, requests, re, threading, time, hashlib, uuid, random, string
from datetime import datetime, timedelta
from functools import wraps
from email.mime.text import MIMEText
import smtplib
from flask_caching import Cache
import socket
import dns.resolver

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this'

# Configuration
STREAMTAPE_USER = os.getenv('STREAMTAPE_USER')
STREAMTAPE_KEY = os.getenv('STREAMTAPE_KEY')
TMDB_API_KEY = os.getenv('TMDB_API_KEY')
API_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_DELAY = 2

# Database files
USERS_DB = "users.json"
MEDIA_DB = "media.json"
TAGS_DB = "tags.json"
ACTIVITY_DB = "activity.json"
SETTINGS_DB = "settings.json"
SUPPORT_REQUESTS_DB = "support_requests.json"
OTP_DB = "otp.json"

# Initialize cache
cache_config = {
    'CACHE_TYPE': 'SimpleCache',
    'CACHE_DEFAULT_TIMEOUT': 300
}
cache = Cache(app, config=cache_config)

# Global DNS settings
dns_servers = None

# TMDB Configuration
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMG_BASE = "https://image.tmdb.org/t/p/w500"

# Initialize databases
def init_databases():
    # Users database
    if not os.path.exists(USERS_DB):
        users_data = {
            "admin": {
                "password": hashlib.sha256("password".encode()).hexdigest(),
                "is_admin": True,
                "created_at": datetime.now().isoformat(),
                "last_activity": None,
                "disabled": False,
                "email_verified": True
            }
        }
        with open(USERS_DB, "w") as f:
            json.dump(users_data, f, indent=2)
    
    # Media database
    if not os.path.exists(MEDIA_DB):
        with open(MEDIA_DB, "w") as f:
            json.dump({}, f, indent=2)
    
    # Tags database
    if not os.path.exists(TAGS_DB):
        with open(TAGS_DB, "w") as f:
            json.dump(["Movie", "TV Show", "Web Series"], f, indent=2)
    
    # Activity database
    if not os.path.exists(ACTIVITY_DB):
        with open(ACTIVITY_DB, "w") as f:
            json.dump({}, f, indent=2)
            
    # Settings database
    if not os.path.exists(SETTINGS_DB):
        settings_data = {
            "theme": "darkTheme",
            "registration_enabled": True,
            "contact_support_enabled": True,
            "smtp": {
                "enabled": False,
                "host": "",
                "port": 587,
                "use_tls": True,
                "username": "",
                "password": "",
                "from_email": ""
            },
            "dns": "default",
            "caching_enabled": True,
            "use_embedded_links": False,
            "use_videojs_for_embedded": False,
            "tmdb_api_enabled": True,
            "tmdb_api_key": "2a8a74a31eaac95133552ff47cd913ae"
        }
        with open(SETTINGS_DB, "w") as f:
            json.dump(settings_data, f, indent=2)
    
    # Support requests database
    if not os.path.exists(SUPPORT_REQUESTS_DB):
        with open(SUPPORT_REQUESTS_DB, "w") as f:
            json.dump([], f, indent=2)
    
    # OTP database
    if not os.path.exists(OTP_DB):
        with open(OTP_DB, "w") as f:
            json.dump({}, f, indent=2)

def load_json(filename):
    try:
        with open(filename, "r") as f:
            return json.load(f)
    except:
        return {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

# Authentication decorators
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        
        # Check if account is disabled
        users = load_json(USERS_DB)
        username = session['username']
        if users.get(username, {}).get('disabled', False):
            session.pop('username', None)
            flash('Your account has been disabled.')
            return redirect(url_for('login'))
            
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'username' not in session:
            return redirect(url_for('login'))
        users = load_json(USERS_DB)
        if not users.get(session['username'], {}).get('is_admin', False):
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# DNS functions
def set_dns(dns_type):
    global dns_servers
    
    if dns_type == 'google':
        dns_servers = ['8.8.8.8', '8.8.4.4']
    elif dns_type == 'cloudflare':
        dns_servers = ['1.1.1.1', '1.0.0.1']
    else:
        dns_servers = None  # Use system default
    
    # Set DNS resolver
    if dns_servers:
        dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
        dns.resolver.default_resolver.nameservers = dns_servers
    else:
        # Reset to system default
        dns.resolver.default_resolver = dns.resolver.Resolver(configure=False)
        dns.resolver.default_resolver.nameservers = ['1.1.1.1', '1.0.0.1']
    
    # Set socket DNS resolution
    if dns_servers:
        # This is a workaround for Python's socket module
        # We'll patch the getaddrinfo function to use our DNS
        original_getaddrinfo = socket.getaddrinfo
        
        def custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
            try:
                # Try to resolve using our custom DNS first
                if dns_servers:
                    resolver = dns.resolver.Resolver()
                    resolver.nameservers = dns_servers
                    answers = resolver.resolve(host, 'A')
                    ip = str(answers[0])
                    return original_getaddrinfo(ip, port, family, type, proto, flags)
            except:
                pass
            # Fall back to original if custom DNS fails
            return original_getaddrinfo(host, port, family, type, proto, flags)
        
        socket.getaddrinfo = custom_getaddrinfo
    else:
        # Restore original getaddrinfo if it was replaced
        if hasattr(socket, '_original_getaddrinfo'):
            socket.getaddrinfo = socket._original_getaddrinfo
        else:
            # Store original if not already stored
            socket._original_getaddrinfo = socket.getaddrinfo

# TMDB API functions
def get_tmdb_data(title, year=None, tmdb_id=None, media_type='movie'):
    settings = load_json(SETTINGS_DB)
    
    # Check if TMDB API is enabled
    if not settings.get('tmdb_api_enabled', True) or not settings.get('tmdb_api_key'):
        # Fallback to placeholder if API is disabled or key not set
        return {
            "poster": f"https://via.placeholder.com/300x450?text={title.replace(' ', '+')}", 
            "cover": f"https://via.placeholder.com/1920x1080?text={title.replace(' ', '+')}",
            "rating": "8.0",
            "cast": ["Actor 1", "Actor 2", "Actor 3"],
            "trailer": None
        }
    
    # Use Cloudflare DNS for TMDB API requests
    try:
        # Set up Cloudflare DNS resolver
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = ['1.1.1.1', '1.0.0.1']
        
        # Resolve TMDB API domain
        domain = "api.themoviedb.org"
        answers = resolver.resolve(domain, 'A')
        ip = answers[0].to_text()
        
        base_url = f"http://{ip}/3"
        api_key = settings.get('tmdb_api_key')
        
        # Determine the endpoint based on media type
        if media_type == 'series':
            search_endpoint = "search/tv"
            details_endpoint = "tv"
        else:
            search_endpoint = "search/movie"
            details_endpoint = "movie"
        
        if tmdb_id:
            url = f"{base_url}/{details_endpoint}/{tmdb_id}?api_key={api_key}"
        else:
            search_url = f"{base_url}/{search_endpoint}?api_key={api_key}&query={title}"
            if year:
                search_url += f"&year={year}"
            
            # Add Host header for the request
            headers = {"Host": domain}
            
            try:
                response = requests.get(search_url, headers=headers, timeout=API_TIMEOUT)
                response.raise_for_status()
                data = response.json()
                
                if not data.get('results'):
                    return None
                    
                tmdb_id = data['results'][0]['id']
                url = f"{base_url}/{details_endpoint}/{tmdb_id}?api_key={api_key}"
            except Exception as e:
                print(f"TMDB search error: {e}")
                return None
        
        try:
            # Add Host header for the request
            headers = {"Host": domain}
            response = requests.get(url, headers=headers, timeout=API_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            
            poster_path = data.get('poster_path')
            poster_url = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else None
            
            backdrop_path = data.get('backdrop_path')
            cover_url = f"https://image.tmdb.org/t/p/original{backdrop_path}" if backdrop_path else None
            
            # Get trailer
            trailer_url = None
            if media_type == 'movie':
                videos_url = f"{base_url}/{details_endpoint}/{tmdb_id}/videos?api_key={api_key}"
                videos_response = requests.get(videos_url, headers=headers, timeout=API_TIMEOUT)
                if videos_response.status_code == 200:
                    videos_data = videos_response.json()
                    for video in videos_data.get('results', []):
                        if video.get('type') == 'Trailer' and video.get('site') == 'YouTube':
                            trailer_url = f"https://www.youtube.com/watch?v={video.get('key')}"
                            break
            elif media_type == 'series':
                seasons_url = f"{base_url}/{details_endpoint}/{tmdb_id}?api_key={api_key}"
                seasons_response = requests.get(seasons_url, headers=headers, timeout=API_TIMEOUT)
                if seasons_response.status_code == 200:
                    seasons_data = seasons_response.json()
                    seasons = seasons_data.get('seasons', [])
                    if seasons:
                        first_season = seasons[0]
                        season_number = first_season.get('season_number', 1)
                        videos_url = f"{base_url}/{details_endpoint}/{tmdb_id}/season/{season_number}/videos?api_key={api_key}"
                        videos_response = requests.get(videos_url, headers=headers, timeout=API_TIMEOUT)
                        if videos_response.status_code == 200:
                            videos_data = videos_response.json()
                            for video in videos_data.get('results', []):
                                if video.get('type') == 'Trailer' and video.get('site') == 'YouTube':
                                    trailer_url = f"https://www.youtube.com/watch?v={video.get('key')}"
                                    break
            
            # Get cast
            cast = []
            credits_url = f"{base_url}/{details_endpoint}/{tmdb_id}/credits?api_key={api_key}"
            credits_response = requests.get(credits_url, headers=headers, timeout=API_TIMEOUT)
            if credits_response.status_code == 200:
                credits_data = credits_response.json()
                for actor in credits_data.get('cast', [])[:5]:
                    cast.append(actor.get('name', ''))
            
            return {
                'poster': poster_url,
                'cover': cover_url,
                'rating': data.get('vote_average', 0),
                'cast': cast,
                'trailer': trailer_url
            }
        except Exception as e:
            print(f"TMDB details error: {e}")
            return None
            
    except Exception as e:
        print(f"DNS resolution error: {e}")
        return None

# New TMDB TV Show functions
def search_tv_show(query):
    settings = load_json(SETTINGS_DB)
    if not settings.get('tmdb_api_enabled', True) or not settings.get('tmdb_api_key'):
        return None
    
    url = f"{TMDB_BASE_URL}/search/tv"
    params = {
        "api_key": settings['tmdb_api_key'],
        "query": query,
        "language": "en-US"
    }
    
    try:
        response = requests.get(url, params=params, timeout=API_TIMEOUT)
        response.raise_for_status()
        results = response.json().get("results", [])
        return results[0] if results else None
    except Exception as e:
        print(f"Error searching TV show: {e}")
        return None

def get_tv_details(tv_id):
    settings = load_json(SETTINGS_DB)
    if not settings.get('tmdb_api_enabled', True) or not settings.get('tmdb_api_key'):
        return None
    
    url = f"{TMDB_BASE_URL}/tv/{tv_id}"
    params = {
        "api_key": settings['tmdb_api_key'],
        "language": "en-US",
        "append_to_response": "videos,credits"
    }
    
    try:
        response = requests.get(url, params=params, timeout=API_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error getting TV details: {e}")
        return None

def get_season_details(tv_id, season_number):
    settings = load_json(SETTINGS_DB)
    if not settings.get('tmdb_api_enabled', True) or not settings.get('tmdb_api_key'):
        return None
    
    url = f"{TMDB_BASE_URL}/tv/{tv_id}/season/{season_number}"
    params = {
        "api_key": settings['tmdb_api_key'],
        "language": "en-US"
    }
    
    try:
        response = requests.get(url, params=params, timeout=API_TIMEOUT)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error getting season details: {e}")
        return None

def save_image(file_path, url):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    try:
        r = requests.get(url, stream=True, timeout=API_TIMEOUT)
        if r.status_code == 200:
            with open(file_path, 'wb') as f:
                for chunk in r.iter_content(1024):
                    f.write(chunk)
            print(f"[+] Saved: {file_path}")
            return True
        else:
            print(f"[!] Failed to download: {url}")
            return False
    except Exception as e:
        print(f"[!] Error downloading image: {e}")
        return False

# Email functions
def send_otp_email(email, otp):
    settings = load_json(SETTINGS_DB)
    smtp_settings = settings.get('smtp', {})
    
    if not smtp_settings.get('enabled'):
        return False
    
    msg = MIMEText(f"Your OTP for Media Server registration is: {otp}")
    msg['Subject'] = 'Media Server - Email Verification'
    msg['From'] = smtp_settings.get('from_email')
    msg['To'] = email
    
    try:
        with smtplib.SMTP(smtp_settings['host'], smtp_settings['port']) as server:
            if smtp_settings.get('use_tls'):
                server.starttls()
            if smtp_settings.get('username') and smtp_settings.get('password'):
                server.login(smtp_settings['username'], smtp_settings['password'])
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

# Streamtape functions - FIXED
def is_streamtape_url(url):
    return bool(re.match(r'https?://(strtape\.tech|streamtape\.com)/', url))

def resolve_streamtape(url):
    try:
        match = re.search(r'/(?:v|e)/([a-zA-Z0-9]+)/', url)
        if not match:
            return url
            
        file_id = match.group(1)
        
        # Get download ticket
        ticket_url = f"https://api.strtape.tech/file/dlticket?file={file_id}&login={STREAMTAPE_USER}&key={STREAMTAPE_KEY}"
        
        for attempt in range(MAX_RETRIES):
            try:
                ticket_response = requests.get(ticket_url, timeout=API_TIMEOUT)
                ticket_response.raise_for_status()
                ticket_data = ticket_response.json()
                break
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    return url
        
        if not ticket_data or ticket_data.get('status') != 200:
            return url
            
        ticket = ticket_data['result']['ticket']
        wait_time = ticket_data['result'].get('wait_time', 0)
        
        if wait_time > 0:
            time.sleep(wait_time + 1)
        
        # Get download URL
        dl_url = f"https://api.strtape.tech/file/dl?file={file_id}&ticket={ticket}&login={STREAMTAPE_USER}&key={STREAMTAPE_KEY}"
        
        for attempt in range(MAX_RETRIES):
            try:
                dl_response = requests.get(dl_url, timeout=API_TIMEOUT)
                dl_response.raise_for_status()
                dl_data = dl_response.json()
                
                if dl_data.get('status') == 200:
                    return dl_data['result']['url']
                else:
                    break
                    
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                else:
                    return url
                    
    except Exception as e:
        print(f"Streamtape resolution error: {e}")
    return url

def refresh_streamtape_links():
    while True:
        settings = load_json(SETTINGS_DB)
        
        # Only refresh if embedded links are not being used
        if not settings.get('use_embedded_links', False):
            print("Refreshing Streamtape links...")
            media_data = load_json(MEDIA_DB)
            updated_count = 0
            
            for media_id, media_info in media_data.items():
                # Skip if this media uses embedded link
                if media_info.get('use_embedded_link', False):
                    continue
                    
                if media_info.get('type') == 'series':
                    # Refresh each episode
                    for season in media_info.get('seasons', []):
                        for episode in season.get('episodes', []):
                            original_url = episode.get('original_url', '')
                            if original_url and is_streamtape_url(original_url):
                                new_url = resolve_streamtape(original_url)
                                if new_url != episode.get('url') and new_url != original_url:
                                    episode['url'] = new_url
                                    updated_count += 1
                                    print(f"Updated {media_id} S{season['season_number']}E{episode['episode_number']}: {new_url}")
                else:
                    # Movie
                    if media_info.get('is_streamtape', False) and 'original_url' in media_info:
                        original_url = media_info['original_url']
                        new_url = resolve_streamtape(original_url)
                        
                        if new_url != media_info['url'] and new_url != original_url:
                            media_data[media_id]['url'] = new_url
                            print(f"Updated {media_id}: {new_url}")
                            updated_count += 1
            
            if updated_count > 0:
                save_json(MEDIA_DB, media_data)
                print(f"Updated {updated_count} Streamtape links")
        else:
            print("Embedded links mode enabled - skipping Streamtape refresh")
        
        time.sleep(300)  # 5 minutes

# HTML Templates (unchanged, keep all the HTML templates as they were)
# ... [All HTML templates remain unchanged] ...
# HTML Templates



# Routes
@app.route('/')
@login_required
def dashboard():
    users = load_json(USERS_DB)
    if users.get(session['username'], {}).get('is_admin', False):
        return redirect(url_for('admin_dashboard'))
    
    media_data = load_json(MEDIA_DB)
    tags = load_json(TAGS_DB)
    
    # Group media by tags
    media_by_tag = {}
    for tag in tags:
        media_by_tag[tag] = [(k, v) for k, v in media_data.items() if v.get('tag') == tag]
        media_by_tag[tag].sort(key=lambda x: x[1].get('year', 0), reverse=True)
    
    return render_template(
    "user_dashboard.html",
    media_by_tag=media_by_tag,
    tags=tags,
    media_json=json.dumps(media_data)
)

@app.route('/play/<media_id>')
@login_required
def play_media(media_id):
    media_data = load_json(MEDIA_DB)
    
    if media_id not in media_data:
        return "Media not found", 404
    
    media_info = media_data[media_id]
    settings = load_json(SETTINGS_DB)
    
    # Track activity
    activity_data = load_json(ACTIVITY_DB)
    username = session['username']
    if username not in activity_data:
        activity_data[username] = []
    
    activity_data[username].append({
        'media_id': media_id,
        'action': 'play',
        'timestamp': datetime.now().isoformat()
    })
    
    # Keep only last 100 activities per user
    if len(activity_data[username]) > 100:
        activity_data[username] = activity_data[username][-100:]
    
    save_json(ACTIVITY_DB, activity_data)
    
    # Update user's last activity and active status
    users = load_json(USERS_DB)
    if username in users:
        users[username]['last_activity'] = datetime.now().isoformat()
        users[username]['is_active'] = True
        save_json(USERS_DB, users)
    
    return render_template(
        "play_media.html",
        media=media_info,
        media_id=media_id,
        settings=settings
    )

@app.route('/browse/<tag>')
@login_required
def browse_tag(tag):
    media_data = load_json(MEDIA_DB)
    tags = load_json(TAGS_DB)
    
    # Filter media by tag
    tag_media = [(k, v) for k, v in media_data.items() if v.get('tag') == tag]
    tag_media.sort(key=lambda x: x[1].get('year', 0), reverse=True)
    
    return render_template(
        "user_dashboard.html",
        media_by_tag={tag: tag_media},
        tags=tags,
        media_json=json.dumps(media_data)
    )

@app.route('/login', methods=['GET', 'POST'])
def login():
    settings = load_json(SETTINGS_DB)
    registration_enabled = settings.get('registration_enabled', True)
    contact_support_enabled = settings.get('contact_support_enabled', True)
    
    account_disabled = False
    attempted_username = None
    
    if request.method == 'POST':
        attempted_username = request.form.get('username', '').lower()
        password = request.form.get('password', '')
        
        users = load_json(USERS_DB)
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        
        if attempted_username in users and users[attempted_username]['password'] == hashed_password:
            # Check if account is disabled
            if users[attempted_username].get('disabled', False):
                account_disabled = True
            else:
                session['username'] = attempted_username
                return redirect(url_for('dashboard'))
        else:
            flash('Invalid username or password')
    
    return render_template(
        "login.html",
        account_disabled=account_disabled,
        contact_support_enabled=contact_support_enabled,
        registration_enabled=registration_enabled,
        attempted_username=attempted_username
    )

@app.route('/contact-support', methods=['GET', 'POST'])
def contact_support():
    settings = load_json(SETTINGS_DB)
    contact_support_enabled = settings.get('contact_support_enabled', True)
    
    if not contact_support_enabled:
        return "Contact support is currently disabled", 403
    
    username = request.args.get('username', '')
    
    if request.method == 'POST':
        username = request.form.get('username', '')
        message = request.form.get('message', '')
        
        if not username or not message:
            flash('Username and message are required')
            return redirect(url_for('contact_support', username=username))
        
        support_requests = load_json(SUPPORT_REQUESTS_DB)
        
        new_request = {
            'id': str(uuid.uuid4()),
            'username': username,
            'message': message,
            'timestamp': datetime.now().isoformat()
        }
        
        support_requests.append(new_request)
        save_json(SUPPORT_REQUESTS_DB, support_requests)
        
        flash('Your message has been sent to the support team. They will contact you soon.')
        return redirect(url_for('login'))
    
    return render_template(
        "contact_support.html",
        username=username
    )

@app.route('/register', methods=['GET', 'POST'])
def register():
    settings = load_json(SETTINGS_DB)
    registration_enabled = settings.get('registration_enabled', True)
    smtp_enabled = settings.get('smtp', {}).get('enabled', False)
    
    if not registration_enabled:
        flash('User registration is currently disabled')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        username = request.form['username'].lower()
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        email = request.form.get('email', '')
        
        if password != confirm_password:
            flash('Passwords do not match')
            return render_template("register.html", email_verification_enabled=smtp_enabled)
        
        users = load_json(USERS_DB)
        
        if username in users:
            flash('Username is already taken')
            return render_template("register.html", email_verification_enabled=smtp_enabled)
        
        # Create user with email verification status
        users[username] = {
            'password': hashlib.sha256(password.encode()).hexdigest(),
            'email': email if smtp_enabled else '',
            'is_admin': False,
            'created_at': datetime.now().isoformat(),
            'last_activity': None,
            'is_active': False,
            'disabled': False,
            'email_verified': not smtp_enabled  # If SMTP is disabled, auto-verify
        }
        
        save_json(USERS_DB, users)
        
        # If SMTP is enabled, send OTP and redirect to verification page
        if smtp_enabled:
            # Generate 6-digit OTP
            otp = ''.join(random.choices(string.digits, k=6))
            
            # Store OTP with expiration (10 minutes)
            otp_data = {
                'otp': otp,
                'expires_at': (datetime.now() + timedelta(minutes=10)).isoformat()
            }
            
            otp_db = load_json(OTP_DB)
            otp_db[username] = otp_data
            save_json(OTP_DB, otp_db)
            
            # Send OTP email
            if send_otp_email(email, otp):
                return redirect(url_for('verify_otp', username=username))
            else:
                flash('Failed to send verification email. Please contact support.')
                return redirect(url_for('login'))
        else:
            # If SMTP is disabled, log in the user directly
            session['username'] = username
            return redirect(url_for('dashboard'))
    
    return render_template("register.html", email_verification_enabled=smtp_enabled)

@app.route('/verify-otp/<username>', methods=['GET', 'POST'])
def verify_otp(username):
    if request.method == 'POST':
        otp = request.form['otp']
        
        otp_db = load_json(OTP_DB)
        users = load_json(USERS_DB)
        
        if username not in otp_db:
            flash('Invalid OTP or OTP expired')
            return redirect(url_for('verify_otp', username=username))
        
        otp_data = otp_db[username]
        
        # Check if OTP is expired
        expires_at = datetime.fromisoformat(otp_data['expires_at'])
        if datetime.now() > expires_at:
            flash('OTP has expired')
            del otp_db[username]
            save_json(OTP_DB, otp_db)
            return redirect(url_for('verify_otp', username=username))
        
        # Check if OTP matches
        if otp == otp_data['otp']:
            # Mark email as verified
            if username in users:
                users[username]['email_verified'] = True
                save_json(USERS_DB, users)
            
            # Remove OTP
            del otp_db[username]
            save_json(OTP_DB, otp_db)
            
            # Log in the user
            session['username'] = username
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid OTP')
            return redirect(url_for('verify_otp', username=username))
    
    return render_template("otp.html", username=username)

@app.route('/logout')
def logout():
    # Update user's active status when logging out
    if 'username' in session:
        users = load_json(USERS_DB)
        username = session['username']
        if username in users:
            users[username]['is_active'] = False
            save_json(USERS_DB, users)
    
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    users = load_json(USERS_DB)
    media_data = load_json(MEDIA_DB)
    tags = load_json(TAGS_DB)
    activity_data = load_json(ACTIVITY_DB)
    settings = load_json(SETTINGS_DB)
    
    # Calculate active users (with activity in the last 5 minutes)
    active_users_count = 0
    current_time = datetime.now()
    
    for username, user_data in users.items():
        if user_data.get('last_activity'):
            last_activity = datetime.fromisoformat(user_data['last_activity'])
            if (current_time - last_activity).seconds < 300:  # 5 minutes
                active_users_count += 1
                users[username]['is_active'] = True
            else:
                users[username]['is_active'] = False
        else:
            users[username]['is_active'] = False
    
    save_json(USERS_DB, users)
    
    # Calculate stats
    stats = {
        'total_media': len(media_data),
        'total_users': len(users),
        'active_users': active_users_count,
        'total_views': sum(len(activities) for activities in activity_data.values())
    }
    
    return render_template(
        "admin_dashboard.html",
        users=users,
        tags=tags,
        stats=stats,
        media_data=media_data,
        streamtape_user=STREAMTAPE_USER,
        streamtape_key='*' * len(STREAMTAPE_KEY),
        settings=settings
    )

@app.route('/admin/create-user', methods=['POST'])
@admin_required
def create_user():
    data = request.get_json()
    users = load_json(USERS_DB)
    
    username = data['username'].lower()
    if username in users:
        return jsonify({'error': 'User already exists'}), 400
    
    users[username] = {
        'password': hashlib.sha256(data['password'].encode()).hexdigest(),
        'email': data.get('email', ''),
        'is_admin': data.get('is_admin', False),
        'created_at': datetime.now().isoformat(),
        'last_activity': None,
        'is_active': False,
        'disabled': False,
        'email_verified': True  # Admin-created users are auto-verified
    }
    
    save_json(USERS_DB, users)
    return jsonify({'success': True})

@app.route('/admin/toggle-user-status/<username>', methods=['PUT'])
@admin_required
def toggle_user_status(username):
    if username == 'admin':
        return jsonify({'error': 'Cannot disable admin user'}), 400
    
    users = load_json(USERS_DB)
    if username in users:
        users[username]['disabled'] = not users[username].get('disabled', False)
        save_json(USERS_DB, users)
        
        # If user is being disabled and is currently logged in, log them out
        if users[username]['disabled'] and username in session:
            session.pop('username', None)
        
        return jsonify({'success': True})
    
    return jsonify({'error': 'User not found'}), 404

@app.route('/admin/delete-user/<username>', methods=['DELETE'])
@admin_required
def delete_user(username):
    if username == 'admin':
        return jsonify({'error': 'Cannot delete admin user'}), 400
    
    users = load_json(USERS_DB)
    if username in users:
        del users[username]
        save_json(USERS_DB, users)
    
    return jsonify({'success': True})

@app.route('/admin/create-tag', methods=['POST'])
@admin_required
def create_tag():
    data = request.get_json()
    tags = load_json(TAGS_DB)
    
    new_tag = data['tag']
    if new_tag not in tags:
        tags.append(new_tag)
        save_json(TAGS_DB, tags)
    
    return jsonify({'success': True})

@app.route('/admin/get-media/<media_id>')
@admin_required
def get_media(media_id):
    media_data = load_json(MEDIA_DB)
    if media_id in media_data:
        return jsonify(media_data[media_id])
    return jsonify({'error': 'Media not found'}), 404

# FIXED: Update media endpoint to properly handle Streamtape URLs
@app.route('/admin/update-media/<media_id>', methods=['PUT'])
@admin_required
def update_media(media_id):
    data = request.get_json()
    media_data = load_json(MEDIA_DB)
    settings = load_json(SETTINGS_DB)
    
    if media_id not in media_data:
        return jsonify({'error': 'Media not found'}), 404
    
    # Get current media info
    current_media = media_data[media_id]
    
    # Get the new URL from the request
    new_url = data['url']
    use_embedded_link = data.get('use_embedded_link', current_media.get('use_embedded_link', False))
    
    # Update IMDB/TMDB data if IDs changed
    if (data.get('imdb_id') != current_media.get('imdb_id') or 
        data.get('tmdb_id') != current_media.get('tmdb_id') or
        data['name'] != current_media['name']):
        tmdb_data = get_tmdb_data(data['name'], data['year'], data.get('tmdb_id'), 
                                  'tv' if current_media.get('type') == 'series' else 'movie')
    else:
        # Keep existing poster/cast data
        tmdb_data = {
            'poster': current_media.get('poster', ''),
            'cover': current_media.get('cover', ''),
            'rating': current_media.get('rating', ''),
            'cast': current_media.get('cast', []),
            'trailer': current_media.get('trailer')
        }
    
    # Handle custom poster upload
    if 'poster_data' in data:
        # Save the base64 image to a file
        import base64
        poster_filename = f"static/posters/{media_id}.jpg"
        os.makedirs(os.path.dirname(poster_filename), exist_ok=True)
        
        with open(poster_filename, 'wb') as f:
            f.write(base64.b64decode(data['poster_data']))
        
        # Update poster URL
        tmdb_data['poster'] = f"/{poster_filename}"
    
    # Update media info - always preserve the original URL
    updated_info = {
        'name': data['name'],
        'year': data['year'],
        'tag': data['tag'],
        'original_url': new_url,  # Always store the original URL from the form
        'imdb_id': data.get('imdb_id', ''),
        'tmdb_id': data.get('imdb_id', ''),
        'poster': tmdb_data['poster'],
        'cover': tmdb_data['cover'],
        'rating': tmdb_data['rating'],
        'cast': tmdb_data['cast'],
        'trailer': tmdb_data.get('trailer'),
        'use_embedded_link': use_embedded_link,
        'updated_at': datetime.now().isoformat()
    }
    
    # Only resolve if it's a Streamtape URL and not using embedded links
    if not use_embedded_link and is_streamtape_url(new_url):
        resolved_url = resolve_streamtape(new_url)
        updated_info['url'] = resolved_url  # Store the resolved URL
        updated_info['is_streamtape'] = True
    else:
        # For non-Streamtape URLs or embedded links
        updated_info['url'] = new_url
        updated_info['is_streamtape'] = False
    
    # Update series data if provided
    if data.get('type') == 'series' and 'seasons' in data:
        updated_info['seasons'] = data['seasons']
    
    media_data[media_id].update(updated_info)
    save_json(MEDIA_DB, media_data)
    
    return jsonify({'success': True})
@app.route('/admin/delete-media/<media_id>', methods=['DELETE'])
@admin_required
def delete_media(media_id):
    media_data = load_json(MEDIA_DB)
    activity_data = load_json(ACTIVITY_DB)
    
    if media_id not in media_data:
        return jsonify({'error': 'Media not found'}), 404
    
    # Remove media
    del media_data[media_id]
    save_json(MEDIA_DB, media_data)
    
    # Clean up activity data for this media
    for username in activity_data:
        activity_data[username] = [
            activity for activity in activity_data[username] 
            if activity.get('media_id') != media_id
        ]
    save_json(ACTIVITY_DB, activity_data)
    
    return jsonify({'success': True})

@app.route('/admin/resolve-streamtape', methods=['POST'])
@admin_required
def resolve_streamtape_endpoint():
    data = request.get_json()
    url = data.get('url', '')
    
    if not url:
        return jsonify({'error': 'URL is required'}), 400
    
    resolved_url = resolve_streamtape(url)
    return jsonify({'resolved_url': resolved_url})

# FIXED: Add media endpoint to properly handle Streamtape URLs
@app.route('/admin/add-media', methods=['POST'])
@admin_required
def add_media():
    data = request.get_json()
    media_data = load_json(MEDIA_DB)
    settings = load_json(SETTINGS_DB)
    
    media_id = str(uuid.uuid4())
    
    # Get TMDB data
    tmdb_data = get_tmdb_data(data['name'], data['year'], data.get('tmdb_id'), 
                              'tv' if data.get('type') == 'series' else 'movie')
    
    use_embedded_link = data.get('use_embedded_link', False)
    
    if data['type'] == 'series':
        # Handle series data
        seasons = data['seasons']
        
        for season in seasons:
            for episode in season['episodes']:
                original_url = episode['url']
                # Always store the original URL
                episode['original_url'] = original_url
                
                # Only resolve if it's a Streamtape URL and not using embedded links
                if original_url and original_url != '#' and not use_embedded_link and is_streamtape_url(original_url):
                    resolved_url = resolve_streamtape(original_url)
                    episode['url'] = resolved_url
                    # Wait 10 seconds before resolving the next one
                    time.sleep(10)
                else:
                    # For non-Streamtape URLs or embedded links
                    episode['url'] = original_url
        
        media_info = {
            'name': data['name'],
            'year': int(data['year']),
            'tag': data['tag'],
            'type': 'series',
            'seasons': seasons,
            'imdb_id': data.get('imdb_id', ''),
            'tmdb_id': data.get('tmdb_id', ''),
            'poster': tmdb_data['poster'],
            'cover': tmdb_data['cover'],
            'rating': tmdb_data['rating'],
            'cast': tmdb_data['cast'],
            'trailer': tmdb_data.get('trailer'),
            'use_embedded_link': use_embedded_link,
            'created_at': datetime.now().isoformat()
        }
    else:
        # Handle movie data
        original_url = data['url']
        
        # Always store the original URL
        media_info = {
            'name': data['name'],
            'year': int(data['year']),
            'tag': data['tag'],
            'type': 'movie',
            'original_url': original_url,  # Always store the original URL
            'imdb_id': data.get('imdb_id', ''),
            'tmdb_id': data.get('tmdb_id', ''),
            'poster': tmdb_data['poster'],
            'cover': tmdb_data['cover'],
            'rating': tmdb_data['rating'],
            'cast': tmdb_data['cast'],
            'trailer': tmdb_data.get('trailer'),
            'use_embedded_link': use_embedded_link,
            'created_at': datetime.now().isoformat()
        }
        
        # Only resolve if it's a Streamtape URL and not using embedded links
        if not use_embedded_link and is_streamtape_url(original_url):
            resolved_url = resolve_streamtape(original_url)
            media_info['url'] = resolved_url  # Store the resolved URL
            media_info['is_streamtape'] = True
        else:
            # For non-Streamtape URLs or embedded links
            media_info['url'] = original_url
            media_info['is_streamtape'] = False
    
    media_data[media_id] = media_info
    save_json(MEDIA_DB, media_data)
    
    return jsonify({'success': True})
	
@app.route('/admin/live-activity')
@admin_required
def live_activity():
    activity_data = load_json(ACTIVITY_DB)
    media_data = load_json(MEDIA_DB)
    
    # Get recent activities (last 5 minutes)
    recent_activities = []
    current_time = datetime.now()
    
    for username, activities in activity_data.items():
        for activity in activities[-5:]:  # Last 5 activities
            activity_time = datetime.fromisoformat(activity['timestamp'])
            if (current_time - activity_time).seconds < 300:  # 5 minutes
                media_info = media_data.get(activity['media_id'], {})
                recent_activities.append({
                    'username': username,
                    'media_name': media_info.get('name', 'Unknown'),
                    'duration': f"{(current_time - activity_time).seconds}s ago"
                })
    
    return jsonify(recent_activities)

@app.route('/admin/active-users')
@admin_required
def active_users():
    users = load_json(USERS_DB)
    activity_data = load_json(ACTIVITY_DB)
    media_data = load_json(MEDIA_DB)
    
    active_users = []
    current_time = datetime.now()
    
    for username, user_data in users.items():
        is_active = False
        current_media = None
        last_activity_str = "Never"
        
        if user_data.get('last_activity'):
            last_activity = datetime.fromisoformat(user_data['last_activity'])
            last_activity_str = f"{(current_time - last_activity).seconds}s ago"
            
            # Check if user was active in the last 5 minutes
            if (current_time - last_activity).seconds < 300:
                is_active = True
                
                # Get current media if available
                if username in activity_data and activity_data[username]:
                    latest_activity = activity_data[username][-1]
                    media_id = latest_activity.get('media_id')
                    if media_id in media_data:
                        current_media = media_data[media_id].get('name', 'Unknown')
        
        active_users.append({
            'username': username,
            'is_active': is_active,
            'current_media': current_media,
            'last_activity': last_activity_str
        })
    
    return jsonify(active_users)

@app.route('/admin/save-system-settings', methods=['POST'])
@admin_required
def save_system_settings():
    data = request.get_json()
    
    # Save system settings
    settings = load_json(SETTINGS_DB)
    settings['registration_enabled'] = data.get('registration_enabled', True)
    settings['contact_support_enabled'] = data.get('contact_support_enabled', True)
    save_json(SETTINGS_DB, settings)
    
    return jsonify({'success': True})

@app.route('/admin/save-smtp-settings', methods=['POST'])
@admin_required
def save_smtp_settings():
    data = request.get_json()
    
    # Save SMTP settings
    settings = load_json(SETTINGS_DB)
    settings['smtp'] = {
        'enabled': data.get('enabled', False),
        'host': data.get('host', ''),
        'port': data.get('port', 587),
        'use_tls': data.get('use_tls', True),
        'username': data.get('username', ''),
        'password': data.get('password', ''),
        'from_email': data.get('from_email', '')
    }
    save_json(SETTINGS_DB, settings)
    
    return jsonify({'success': True})

@app.route('/admin/save-dns-settings', methods=['POST'])
@admin_required
def save_dns_settings():
    data = request.get_json()
    dns_type = data.get('dns', 'default')
    
    # Save DNS settings
    settings = load_json(SETTINGS_DB)
    settings['dns'] = dns_type
    save_json(SETTINGS_DB, settings)
    
    # Apply DNS settings
    set_dns(dns_type)
    
    return jsonify({'success': True})

@app.route('/admin/save-caching-settings', methods=['POST'])
@admin_required
def save_caching_settings():
    data = request.get_json()
    caching_enabled = data.get('caching_enabled', True)
    
    # Save caching settings
    settings = load_json(SETTINGS_DB)
    settings['caching_enabled'] = caching_enabled
    save_json(SETTINGS_DB, settings)
    
    # Clear cache if disabling
    if not caching_enabled:
        cache.clear()
    
    return jsonify({'success': True})

@app.route('/admin/save-theme', methods=['POST'])
@admin_required
def save_theme():
    data = request.get_json()
    theme = data.get('theme', 'darkTheme')
    
    # Save theme preference to settings file
    settings = load_json(SETTINGS_DB)
    settings['theme'] = theme
    save_json(SETTINGS_DB, settings)
    
    return jsonify({'success': True})

@app.route('/admin/save-tmdb-settings', methods=['POST'])
@admin_required
def save_tmdb_settings():
    data = request.get_json()
    
    # Save TMDB settings
    settings = load_json(SETTINGS_DB)
    settings['tmdb_api_enabled'] = data.get('tmdb_api_enabled', True)
    settings['tmdb_api_key'] = data.get('tmdb_api_key', '')
    save_json(SETTINGS_DB, settings)
    
    return jsonify({'success': True})

@app.route('/admin/test-tmdb-api', methods=['POST'])
@admin_required
def test_tmdb_api():
    data = request.get_json()
    query = data.get('query', '')
    
    if not query:
        return jsonify({'success': False, 'error': 'No query provided'})
    
    # Test the TMDB API with the provided query
    result = get_tmdb_data(query)
    
    if result:
        return jsonify({
            'success': True, 
            'result': f"Poster: {result['poster'][:50]}..."
        })
    else:
        return jsonify({
            'success': False, 
            'error': 'Failed to fetch data from TMDB'
        })

@app.route('/admin/support-requests')
@admin_required
def support_requests():
    settings = load_json(SETTINGS_DB)
    contact_support_enabled = settings.get('contact_support_enabled', True)
    
    if not contact_support_enabled:
        return jsonify({'error': 'Contact support is disabled'}), 403
    
    support_requests = load_json(SUPPORT_REQUESTS_DB)
    return jsonify(support_requests)

@app.route('/admin/delete-support-request/<request_id>', methods=['DELETE'])
@admin_required
def delete_support_request(request_id):
    support_requests = load_json(SUPPORT_REQUESTS_DB)
    
    # Find and remove the request
    for i, request in enumerate(support_requests):
        if request['id'] == request_id:
            support_requests.pop(i)
            save_json(SUPPORT_REQUESTS_DB, support_requests)
            return jsonify({'success': True})
    
    return jsonify({'error': 'Request not found'}), 404

# New TMDB TV Show endpoints
@app.route('/admin/search-tmdb-tv', methods=['POST'])
@admin_required
def search_tmdb_tv():
    data = request.get_json()
    query = data.get('query', '')
    
    if not query:
        return jsonify({'error': 'Query is required'}), 400
    
    result = search_tv_show(query)
    if result:
        return jsonify({'results': [result]})
    else:
        return jsonify({'error': 'No results found'}), 404

@app.route('/admin/get-tmdb-tv-details', methods=['POST'])
@admin_required
def get_tmdb_tv_details():
    data = request.get_json()
    tv_id = data.get('tv_id')
    
    if not tv_id:
        return jsonify({'error': 'TV ID is required'}), 400
    
    result = get_tv_details(tv_id)
    if result:
        return jsonify(result)
    else:
        return jsonify({'error': 'Failed to get TV details'}), 404

@app.route('/admin/get-tmdb-season-details', methods=['POST'])
@admin_required
def get_tmdb_season_details():
    data = request.get_json()
    tv_id = data.get('tv_id')
    season_number = data.get('season_number')
    
    if not tv_id or not season_number:
        return jsonify({'error': 'TV ID and season number are required'}), 400
    
    result = get_season_details(tv_id, season_number)
    if result:
        return jsonify(result)
    else:
        return jsonify({'error': 'Failed to get season details'}), 404

@app.route('/admin/save-tmdb-images', methods=['POST'])
@admin_required
def save_tmdb_images():
    data = request.get_json()
    tv_id = data.get('tv_id')
    poster_url = data.get('poster_url')
    backdrop_url = data.get('backdrop_url')
    
    if not tv_id:
        return jsonify({'error': 'TV ID is required'}), 400
    
    # Create media directory if it doesn't exist
    media_dir = f"static/media/{tv_id}"
    os.makedirs(media_dir, exist_ok=True)
    
    # Save poster
    if poster_url:
        poster_path = f"{media_dir}/poster.jpg"
        save_image(poster_path, poster_url)
    
    # Save backdrop
    if backdrop_url:
        backdrop_path = f"{media_dir}/backdrop.jpg"
        save_image(backdrop_path, backdrop_url)
    
    return jsonify({'success': True})

@app.route('/track-activity', methods=['POST'])
@login_required
def track_activity():
    data = request.get_json()
    activity_data = load_json(ACTIVITY_DB)
    users = load_json(USERS_DB)
    
    username = session['username']
    if username not in activity_data:
        activity_data[username] = []
    
    activity_data[username].append({
        'media_id': data['media_id'],
        'action': data['action'],
        'timestamp': datetime.now().isoformat()
    })
    
    # Keep only last 100 activities per user
    if len(activity_data[username]) > 100:
        activity_data[username] = activity_data[username][-100:]
    
    save_json(ACTIVITY_DB, activity_data)
    
    # Update user's last activity and active status
    if username in users:
        users[username]['last_activity'] = datetime.now().isoformat()
        users[username]['is_active'] = True
        save_json(USERS_DB, users)
    
    return jsonify({'success': True})

@app.route('/stream/<media_id>')
@login_required
def stream_media(media_id):
    media_data = load_json(MEDIA_DB)
    
    if media_id not in media_data:
        return "Media not found", 404
    
    media_info = media_data[media_id]
    
    # For Streamtape links, redirect to direct URL
    if media_info.get('is_streamtape', False) and not media_info.get('use_embedded_link', False):
        return redirect(media_info['url'])
    
    # For other links, proxy the content
    remote_url = media_info['url']
    headers = {}
    if 'Range' in request.headers:
        headers['Range'] = request.headers['Range']
    
    try:
        r = requests.get(remote_url, headers=headers, stream=True, timeout=20)
        r.raise_for_status()
    except Exception as e:
        print(f"Stream error: {e}")
        return "Stream failed", 502
    
    return Response(
        r.iter_content(32 * 1024),
        status=r.status_code,
        headers={k: v for k, v in r.headers.items()
                 if k.lower() in ['content-type', 'content-length', 'accept-ranges', 'content-range']}
    )

if __name__ == '__main__':
    init_databases()
    
    # Load settings and apply DNS configuration
    settings = load_json(SETTINGS_DB)
    set_dns(settings.get('dns', 'default'))
    
    # Start Streamtape refresh thread
    refresh_thread = threading.Thread(target=refresh_streamtape_links, daemon=True)
    refresh_thread.start()
    
    # Start user activity check thread (mark inactive users)
    def check_user_activity():
        while True:
            time.sleep(60)  # Check every minute
            users = load_json(USERS_DB)
            current_time = datetime.now()
            
            for username, user_data in users.items():
                if user_data.get('last_activity'):
                    last_activity = datetime.fromisoformat(user_data['last_activity'])
                    if (current_time - last_activity).seconds > 300:  # 5 minutes
                        user_data['is_active'] = False
            
            save_json(USERS_DB, users)
    
    activity_check_thread = threading.Thread(target=check_user_activity, daemon=True)
    activity_check_thread.start()
    
    print("🎬 Media Server Starting...")
    print(f"📊 Admin Login: username=admin, password=password")
    print(f"🌐 Server running on: http://0.0.0.0:5002")
    print(f"📺 Streamtape auto-refresh: Every 5 minutes")
    print(f"👥 User activity check: Every minute")
    print(f"🔧 DNS setting: {settings.get('dns', 'default')}")
    print(f"⚡ Caching: {'Enabled' if settings.get('caching_enabled', True) else 'Disabled'}")
    print(f"🎬 TMDB API: {'Enabled' if settings.get('tmdb_api_enabled', True) else 'Disabled'}")
    print(f"🔗 Embedded Links: {'Enabled' if settings.get('use_embedded_links', False) else 'Disabled'}")
    print(f"🎬 Video.js for Embedded: {'Enabled' if settings.get('use_videojs_for_embedded', False) else 'Disabled'}")
    
    app.run(host='0.0.0.0', port=5003, debug=True)
