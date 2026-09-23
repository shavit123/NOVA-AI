# NOVA - AI Assistant

Advanced AI assistant with chat, image generation, voice, and more.

## ✨ Features

- 💬 Smart chat (Hebrew, English, Russian - auto-detected)
- 🎨 Image generation with NVIDIA FLUX
- 🎤 Voice mode with TTS
- 💾 Persistent memory & chat history
- 🤖 Custom AI agents
- 🎯 Deep thinking mode
- 🌐 Web search
- 📱 Mobile-friendly UI
- 🎮 CONTROL MODE for PC (Windows)
- 👑 Admin panel

---

## 🚀 Quick Start

### 1. Clone or download this repository

```bash
git clone https://github.com/YOUR_USERNAME/NOVA-AI.git
cd NOVA-AI
```

### 2. Get API Keys (both FREE)

#### 🤖 Groq API Key (for chat) — REQUIRED

1. Go to **https://console.groq.com/keys**
2. Sign up with Google / GitHub
3. Click **"Create API Key"**
4. Copy the key — looks like `gsk_abc123...`

#### 🎨 NVIDIA API Key (for images) — OPTIONAL

1. Go to **https://build.nvidia.com**
2. Sign up with email
3. Go to **https://build.nvidia.com/black-forest-labs/flux_1-schnell**
4. Click **"Get API Key"** or **"Deploy"**
5. Copy the key — looks like `nvapi-abc123...`

**NVIDIA gives you 1000 free credits per month** — enough for ~1000 images.

### 3. Run SETUP.BAT

Double-click **`SETUP.BAT`**. It will:
- Ask for your Groq API key (required)
- Ask for your NVIDIA API key (optional)
- Save them to a `.env` file (local, private)

### 4. Run START.BAT

Double-click **`START.BAT`**. It will:
- Install dependencies
- Start the server at `http://localhost:8080`
- Open your browser automatically

---

## 🖥️ System Requirements

- **Python 3.9+** — [Download](https://www.python.org/downloads/)
- **Windows 10/11** (for CONTROL MODE)
- **~50 MB** disk space

---

## 📁 Project Structure

```
NOVA-AI/
├── nova.py           # Main server (Python + embedded HTML)
├── requirements.txt  # Python dependencies
├── SETUP.BAT         # First-time setup wizard
├── START.BAT         # Start the server
├── .env              # Your API keys (auto-generated, not in git)
├── .gitignore        # Files to ignore
└── nova.db           # SQLite database (auto-generated)
```

---

## ⚙️ Configuration

The `.env` file supports:

```env
# Required
GROQ_API_KEY=gsk_...

# Optional (for images)
NVIDIA_API_KEY=nvapi-...

# Optional (change DEV password)
DEV_PASSWORD=2214

# Optional (change VIP code)
VIP_CODE=0000

# Optional (change auto-login username)
AUTO_USER=user
```

---

## 🎯 Usage

Once running, open **http://localhost:8080** in your browser.

You'll be automatically logged in as the owner.

### Commands

| Command | Action |
|---------|--------|
| `/sk` then `2214` | Activate DEV MODE |
| `/vip` then `0000` | Activate VIP |
| `/control` | Open CONTROL PANEL (admin only) |
| `/voice` | Start voice mode |

### Image Generation

Just write:
- **Hebrew:** `צור תמונה של חתול`
- **English:** `Create an image of a cat`
- **Russian:** `Нарисуй кота`

The prompt is auto-translated to English for better results.

---

## 🚀 Deploy to Render (Free)

1. Push to GitHub
2. Create **Web Service** on [render.com](https://render.com)
3. Connect your GitHub repo
4. Set:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python nova.py`
5. Add Environment Variables:
   - `GROQ_API_KEY` = your Groq key
   - `NVIDIA_API_KEY` = your NVIDIA key
6. Deploy

**Note:** Render Free sleeps after 15 minutes of inactivity. Use [UptimeRobot](https://uptimerobot.com) to keep it awake.

---

## 📜 License

Personal use only. See LICENSE.

---

## 🙏 Credits

Built by **Shavit Klein**.

API providers:
- [Groq](https://groq.com) — AI chat
- [NVIDIA NIM](https://build.nvidia.com) — Image generation
