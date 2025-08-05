# Homily Podcast Tool

[![Python Version](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/release/python-3120/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

An efficient, automated tool for processing and sharing Catholic homilies. It downloads recordings from S3, extracts homily segments, summarizes with AI, detects content variations, and uploads drafts to WordPress podcasts. Built for reliability and ease of use.

## Features

- **S3 Monitoring**: Automatically fetches recent Mass MP3s from S3 storage.
- **Audio Processing**: Transcribes and extracts homily sections using FFmpeg and VTT analysis.
- **AI Summarization**: Generates titles, descriptions, and context notes with OpenAI GPT.
- **Deviation Detection**: Compares weekend homilies and notifies of significant differences.
- **WordPress Integration**: Uploads homily drafts as podcasts via Seriously Simple Podcasting.
- **Database Management**: SQLite for tracking analyses and comparison states.
- **Email Notifications**: Alerts for errors or deviations.
- **Modular Design**: Structured for easy maintenance and future enhancements.

## Installation

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

5. **FFmpeg**: Ensure FFmpeg is installed and in your PATH. Download from [https://ffmpeg.org/download.html](https://ffmpeg.org/download.html) or use Winget: `winget install Gyan.FFmpeg`.

## Usage

Run the main script:
````bash
python main.py
````   

### CLI Options
- `--test`: Send a test email alert.
- `--latest`: Process the latest MP3 (batch + GPT analysis).
- `--analyze-latest`: Analyze the latest transcript.
- `--extract-latest`: Extract homily from the latest MP3 + VTT.
- `--upload-latest`: Upload the latest extracted homily to WordPress as a draft.
- `--extract`: Extract homily for specific Mass-YYYY-MM-DD_HH-MM.mp3 (e.g., --extract 2025-07-20_18-00).
- `--upload`: Upload specific Homily-YYYY-MM-DD_HH-MM.mp3 to WordPress (e.g., --upload 2025-07-20_18-00).

The script runs in monitoring mode by default, polling S3 every 60 seconds.

## Configuration

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
  },
  "gpt_title_addon": "Ensure the title is inspirational and captures the essence of the Gospel message.",
  "gpt_description_addon": "Phrase the description in a welcoming, faith-building style that encourages listeners to reflect on their spiritual life.",
  "gpt_image_addon": "Render the image in a stained glass style with vibrant colors and Catholic iconography."
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
├── requirements.txt
├── TranscribeHomilies.bat
└── TranscribeWildcard.bat
````

## Running as a Windows Service
To run the tool constantly as a background service on Windows, use WinSW (Windows Service Wrapper) from https://github.com/winsw/winsw. WinSW allows wrapping your Python script in an EXE or running it directly as a service.
Setup Steps

1. Build the One-File EXE:
 * Run PyInstaller in your project directory to create the executable:
````text
pyinstaller.exe --onefile --name homilymonitor main.py
````
 * This generates homilymonitor.exe in the dist folder. Copy config.json to the dist directory alongside the EXE.
2. Download WinSW: Download the latest WinSW.exe from the releases page (e.g., WinSW-x64.exe) and rename it to something like homilymonitor_service.exe in your project directory.
3. Create XML Configuration: Create a file named homilymonitor_service.xml in the same directory as the EXE, with the following content (adjust paths as needed):

````xml
<service>
  <id>HomilyMonitor</id>
  <name>Homily Monitor Service</name>
  <description>Monitors and processes Mass recordings for homily podcasting.</description>
  <executable>%BASE%\homilymonitor.exe</executable>
  <workingdirectory>%BASE%</workingdirectory>
  <logmode>rotate</logmode>
  <logpath>%BASE%\logs</logpath>
  <onfailure action="restart" delay="10 sec"/>
</service>
````

4. Install the Service:
 * Open Command Prompt as Administrator.
 * Run:
````text
homilymonitor_service.exe install
````

5. Run the Service:
 * Run:
````text
homilymonitor_service.exe start
````
 * Check status with homilymonitor_service.exe status or in the Services management console (services.msc).

6. Uninstall (if needed):
 * Run:
````text
homilymonitor_service.exe uninstall
````

This setup ensures your homily monitoring tool runs continuously in the background, automatically processing new recordings as they are uploaded to S3.

## Batch Files for Transcription
The tool uses batch files for transcribing MP3 files with Whisper. Place these in the project directory alongside main.py.

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