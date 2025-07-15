# Homily Podcast Tool

[![Python Version](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

An efficient, automated tool for processing and sharing Catholic homilies. It downloads recordings from S3, extracts homily segments, summarizes with AI, detects content variations, and uploads drafts to WordPress podcasts. Built for reliability and ease of use.

## 🌟 Features

- **S3 Monitoring**: Automatically fetches recent Mass MP3s from S3 storage.
- **Audio Processing**: Transcribes and extracts homily sections using FFmpeg and VTT analysis.
- **AI Summarization**: Generates titles, descriptions, and context notes with OpenAI GPT.
- **Deviation Detection**: Compares weekend homilies and notifies of significant differences.
- **WordPress Integration**: Uploads homily drafts as podcasts via Seriously Simple Podcasting.
- **Database Management**: SQLite for tracking analyses and comparison states.
- **Email Notifications**: Alerts for errors or deviations.
- **Modular Design**: Structured for easy maintenance and future enhancements.

## 🚀 Installation

1. **Clone the Repository**:
````bash
git clone https://github.com/kjrose/homily-podcast-tool.git
cd homily-podcast-tool
````

2. **Set Up Virtual Environment**:
````bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
````
3. **Install Dependencies**:
````bash
pip install -r requirements.txt
````

4. **Configure**:
Copy `config.json.sample` to `config.json` and fill in your details (API keys, paths, etc.).

5. **FFmpeg**: Ensure FFmpeg is installed and in your PATH.

## 📖 Usage

Run the main script:
````bash
python main.py
````   

### CLI Options
- `--test`: Send a test email alert.
- `--latest`: Process the latest MP3 (batch + GPT analysis).
- `--analyze-latest`: Analyze the latest transcript.
- `--extract-latest-homily`: Extract homily from the latest MP3 + VTT.

The script runs in monitoring mode by default, polling S3 every 60 seconds.

## ⚙️ Configuration

`config.json` structure (see `config.json.sample` for a template):
```json
{
  "openai_api_key": "sk-...",
  "s3": {
    "endpoint": "https://s3.example.com",
    "bucket": "your-bucket",
    "folder": "masses/",
    "access_key": "...",
    "secret_key": "..."
  },
  "paths": {
    "local_dir": "./downloads",
    "batch_file": "./TranscribeHomilies.bat",
    "db_path": "./homilies.db"
  },
  "email": {
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "from": "alert@example.com",
    "to": "you@example.com",
    "user": "...",
    "password": "...",
    "subject": "Homily Alert"
  },
  "wordpress": {
    "url": "https://your-site.com",
    "user": "wp-user",
    "app_password": "..."
  }
}
```

## 🛠️ Project Structure
````
homily-podcast-tool/
├── config.json.sample
├── homily_monitor/
    ├── __init__.py
    ├── audio_utils.py
    ├── config_loader.py
    ├── database.py
    ├── email_utils.py
    ├── gpt_utils.py
    ├── helpers.py
    ├── s3_utils.py
    ├── wordpress_utils.py
├── LICENSE
├── main.py  # Entry point
├── README.md
└── requirements.txt
````

## 🤝 Contributing
1. Fork the repository.
2. Create a new branch: `git checkout -b feature/your-feature`.
3. Make your changes and commit: `git commit -m 'Add your feature'`.
4. Push to the branch: `git push origin feature/your-feature`.
5. Open a pull request.
Follow PEP8 and use Black for formatting

## 📄 License
MIT License. See [LICENSE](LICENSE) for details.
----
Designed and developed by Panda Rose Consulting Studios, Inc.  for Holy Trinity Catholic Church in Spruce Grove, AB. Questions? Open an issue!