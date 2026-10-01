# Set up Homily Studio on your WordPress site

Homily Studio connects your own WordPress installation to your own Homily Monitor service. Administrators edit creative instructions and test previews in WordPress; the service retrieves active settings and runs generation using its local OpenAI credentials. The plugin contains no parish connection or central hosted dependency.

## Requirements

- WordPress 6.2 or later, PHP 8.1 or later, and a canonical HTTPS site URL.
- The Python service with the dependencies in `requirements.txt`, its local configuration, and working text/image API access.
- A publishing integration exposing the desired WordPress REST collection. A podcast plugin may provide `podcast`; regular WordPress posts use `posts`.
- A separate worker process for interactive previews. The normal monitor does not process preview jobs.

## Install the plugin

From the repository root, run:

```text
python tools/package_wordpress_plugin.py
```

This creates `dist/homily-studio.zip`, containing an explicit list of plugin source files. It excludes local configuration and runtime data. In WordPress, choose **Plugins → Add New → Upload Plugin**, upload the ZIP, and activate it. Alternatively, copy `wordpress-plugin/homily-studio` into `wp-content/plugins` and activate it.

Open **Homily Studio** in the admin menu. Edit the default draft, then activate it when ready. The site voice, audience, language, title/description instructions, image instructions, and email templates belong to this installation. See [Editorial profiles](editorial-profiles.md).

## Create a dedicated account

Create a WordPress user with one of these roles:

| Role | Permissions |
| --- | --- |
| Homily Settings Reader | Read active settings only |
| Homily Preview Worker | Read active settings, claim queued tests, and return test results |

Generate an Application Password on that user's profile. Keep this account separate from both administrators and the existing media/post publishing account. Application Passwords authenticate a user; the user's capabilities and endpoint checks determine what it can do. [WordPress authentication](https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/).

Plugin settings writes require an administrator browser session and a WordPress REST nonce. The reader and worker roles cannot edit, restore, or activate profiles, create tests, view the private gallery, or apply images to posts. Anonymous users and ordinary subscribers cannot access active settings. [WordPress endpoint permissions](https://developer.wordpress.org/rest-api/extending-the-rest-api/adding-custom-endpoints/).

## Configure the Python service

Use the existing ignored `config.json`, copied from `config.json.sample`. Set `wordpress.url` to your canonical HTTPS site URL; WordPress installed in a subdirectory is supported. Do not add `/wp-json` to this value.

Add this section:

```json
"homily_studio": {
  "enabled": true,
  "user": "your_studio_service_user",
  "app_password": "your_studio_application_password"
}
```

You can instead supply `HOMILY_STUDIO_USER` and `HOMILY_STUDIO_APP_PASSWORD` through the service environment or the ignored local `.env` file. These override the Studio credentials in JSON. They do not change the publishing credentials under `wordpress`.

The service requests `https://example.org/wp-json/homily-studio/v1/settings` using its dedicated credentials. It refuses redirects and non-HTTPS URLs. Use the final site URL after any host, scheme, or subdirectory redirects. Allow outbound HTTPS from the worker to your WordPress site and the configured AI provider.

With Studio disabled, the service uses its local profile and the existing `gpt_title_addon`, `gpt_description_addon`, and `gpt_image_addon` settings. With Studio enabled, WordPress's active profile supplies the complete creative instructions. Settings errors fall back to the most recent validated active profile for that exact site, then to local defaults if no cache exists. A warning appears in the local service log.

## Publishing compatibility

The plugin does not register a podcast post type or replace a podcast publishing plugin. Configure the service for the REST collection and metadata fields exposed by your integration:

```json
"wordpress": {
  "url": "https://example.org",
  "user": "your_publishing_user",
  "app_password": "your_publishing_application_password",
  "post_rest_base": "podcast",
  "meta_fields": {
    "audio": "audio_file",
    "cover_url": "cover_image",
    "cover_id": "cover_image_id"
  }
}
```

Your publishing account needs permission to upload media and create/edit that post type. Your podcast integration must register its metadata for REST writes. Studio does not register or overwrite another plugin's audio metadata.

For ordinary WordPress posts, set `post_rest_base` to `posts` and `meta_fields` to `{}`. The uploader adds a WordPress audio shortcode to the description and uses the standard featured image. Uploads remain drafts.

When applying a preview image in the lab, enter the cover URL and ID metadata keys used by your integration; leave both empty for featured-image-only posts. Applying an image copies it into the public media library and updates only the selected draft's cover. It does not publish the post.

## Start the preview worker

Run this as a second process alongside the normal monitor, using the same local configuration and account environment:

```text
python main.py --studio-worker
```

For a scheduled task or a single-job check:

```text
python main.py --studio-worker-once
```

The worker polls every five seconds when idle. WordPress atomically claims each queued job, so multiple workers cannot claim the same pending job. Completed results are saved locally before delivery; upload retries reuse the result without another AI call. AI request retries are disabled for previews. An interrupted or failed job is not automatically regenerated. Queue a new test explicitly when needed.

In WordPress, choose **Preview lab**, supply sample metadata or select an existing post, and paste its homily transcript. Text tests need a recording filename such as `Mass-2026-10-01_09-00.mp3`. **Use the saved transcript on the homily server** works for mapped posts uploaded after Studio was enabled, using context stored locally after successful upload. Older posts and missing context require pasted transcripts. Image context is limited to the first 4,000 normalized characters, matching production.

Select a text test, concept test, image test, or full test. A concept test lets you review the exact final prompt and then render it without generating a different concept. Comparing with active settings creates two jobs; use **Compare** on a result to display it beside another. Tests do not save production homily analysis, send emails, or change posts.

There is a server-enforced limit of 20 preview jobs per UTC day. Each image/full job renders one 1024 × 1024 PNG. The service's configured AI models are used for both previews and production; WordPress cannot select arbitrary models or endpoints. API usage is recorded when returned by the provider. The UI's default-model image output estimates exclude input and text generation charges. [OpenAI image generation and costs](https://developers.openai.com/api/docs/guides/image-generation).

## Packaged Windows services

New source builds can bundle the catalogue with:

```text
pyinstaller --onedir --name homilymonitor --add-data "homily_monitor/editorial-catalog.json;homily_monitor" main.py
```

For an existing PyInstaller spec, add `('homily_monitor/editorial-catalog.json', 'homily_monitor')` to its `datas` list, or copy `editorial-catalog.json` next to the executable. `Deploy-HomilyMonitor.ps1` copies that public catalogue alongside the deployed executable and backs it up with the application. It leaves local Studio state intact.

Start a separate worker service or scheduled task with `homilymonitor.exe --studio-worker` or `--studio-worker-once`. Do not replace the normal monitor's arguments with worker arguments. Source and packaged workers store their state relative to their runtime base directory, not the shell's working directory.

## Privacy and retention

WordPress stores profiles, private test inputs, and preview PNGs in its database. Viewing them requires an administrator browser session; no preview file is placed in public uploads until **Use this image** is selected. Supplied source text is sent by the worker to the AI provider. The normal WordPress database backup also contains these private records.

Local `.homily-studio/` state contains the site-bound active-profile cache, undelivered results, and saved source context. It is ignored by Git. Protect it as application data and periodically remove source context no longer needed. Each analysed homily stores its editorial snapshot in the local database, preserving instructions for later image creation and upload. Existing rows without a snapshot use current active settings at upload time.

Unstarred WordPress previews are removed after seven days by WP-Cron, which depends on site traffic unless a real cron runner is configured. Kept previews remain until unstarred. Profile history retains 25 previous active versions. Deactivation stops scheduled cleanup and preserves data. Uninstall also preserves data; after backing up, a site administrator can remove the site's prefixed `homily_studio_jobs` table, the `hs_active_profile`, `hs_draft_profile`, `hs_profile_history`, and `hs_write_lock` options, and the two Studio roles if permanent removal is intended.

## Verify and troubleshoot

1. Without authentication, requesting `/settings` should return a permission error. A reader should receive the active profile and fail to access admin or worker operations.
2. Save a draft and verify that `/settings` still returns the active revision. Activate the draft and verify the revision changes on the next new homily.
3. Queue a text preview and run the worker once. Inspect its output before rendering an image.
4. Check that image, source, and job endpoints require an admin session; do not configure a CDN to cache authenticated Studio routes.

If authentication fails, check the role and Application Password, HTTPS, and whether the web server or security plugin strips the Authorization header. If the plugin UI reports a nonce error, reload it after logging in. A REST 404 usually indicates a missing plugin, wrong site URL, or blocked REST routes. A permanently queued job means the worker is stopped or cannot reach WordPress. API failures require checking the worker's model access and local API credentials. Interrupted jobs become failed after 30 minutes; requeue only after checking whether the earlier generation already produced a result.

Offline repository checks are:

```text
python -m unittest discover -s tests -v
php tests/wordpress-plugin-contract.php
node --check wordpress-plugin/homily-studio/assets/admin.js
node tests/admin-workspace.cjs
```

The PHP contract harness exercises plugin callbacks and permissions with WordPress API stubs. It complements, and does not replace, verification on your staging WordPress installation.
