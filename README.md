# 🎬 Media Server

A full-stack media streaming server built with **Flask**, featuring user authentication, admin dashboard, and movie/series management.

---

## 🚀 Features

* 🔐 User Authentication (Login / Register / OTP Verification)
* 👑 Admin Panel (Full Control)
* 🎬 Add / Edit / Delete Movies & Series
* 📺 Stream Media (Direct & Embedded Links)
* 🧠 TMDB Integration (Posters, Ratings, Cast)
* 📊 User Activity Tracking
* 📡 Streamtape Auto Refresh System
* ⚙️ System Settings (SMTP, DNS, Caching, Theme)

---

## 📁 Project Structure

```
media-server/
│
├── app.py
├── users.json
├── media.json
├── settings.json
│
├── templates/
│   ├── login.html
│   ├── register.html
│   ├── otp.html
│   ├── admin_dashboard.html
│   └── user_dashboard.html
│
└── static/ (optional)
```

---

## ⚙️ Installation & Setup

### 1. Clone Repository

```bash
git clone https://github.com/YOUR_USERNAME/media-server.git
cd media-server
```

### 2. Install Dependencies

```bash
pip install flask requests flask-caching dnspython
```

### 3. Run Server

```bash
python app.py
```

---

## 🌐 Access

Open browser:

```
http://127.0.0.1:5003
```

---

## 🔑 Default Admin Login

```
Username: admin
Password: password
```

> ⚠️ Note: Admin is created automatically if `users.json` does not exist.

---

## 📦 Environment Variables (.env) [Optional]

Create a `.env` file for advanced features:

```
STREAMTAPE_USER=your_user
STREAMTAPE_KEY=your_key
TMDB_API_KEY=your_tmdb_key
SECRET_KEY=your_secret
```

---

## ⚠️ Important Notes

* Do NOT upload:

  * `users.json`
  * `otp.json`
  * `activity.json`
* These contain runtime data and should remain local.

---

## 🚀 Future Improvements

* 🌐 Deployment (Render / Railway)
* 🎨 UI Enhancements
* 🔐 Role-Based Access Control
* 📱 Mobile Responsive UI

---

## 👨‍💻 Author

**Shubham Tiwari**

---

## ⭐ Support

If you like this project, give it a ⭐ on GitHub!
