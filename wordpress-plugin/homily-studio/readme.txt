=== Homily Studio ===
Requires at least: 6.2
Requires PHP: 8.1
Stable tag: 1.0.0
License: MIT

Editorial profiles and private title, description, and image previews for Homily Monitor.

== Installation ==
Upload homily-studio.zip through Plugins > Add New > Upload Plugin, then activate it.
Open Homily Studio in the admin menu. Create a dedicated Homily Preview Worker user
and generate an Application Password on that user's profile. Configure the Python
service with your own site's HTTPS URL and dedicated credentials. Run the service's
preview worker alongside its normal monitor. No central site or API key is embedded
in this plugin. The Python service holds the OpenAI key.

Full setup: docs/wordpress-setup.md in the repository.
Editorial workflow: docs/editorial-profiles.md in the repository.

== Privacy ==
Profiles, supplied sample text, and private preview images are stored in this site's
WordPress database. Previews require an administrator browser session to view.
Samples are sent by your worker to your configured OpenAI API. Unstarred previews
are removed after seven days by WP-Cron. Kept previews remain until unstarred.
Applying an image explicitly copies it into WordPress's public media library.
Deactivation preserves profiles and previews; it stops scheduled cleanup. This
plugin does not delete stored data on uninstall. See the setup guide for removal.

== Styles ==
Includes 13 original homily style presets. Artistic traditions and source links
are inspired by John Hartnup's catalogue at https://john.hartnup.uk/poster-prompts/.
The example images and original event-poster prompt text are not redistributed.

== Changelog ==
= 1.0.0 =
Draft/active profiles, history, import/export, private preview queue, and worker roles.
