<?php
/**
 * Plugin Name: Homily Studio
 * Description: Site editorial profiles and private prompt previews for Homily Monitor.
 * Version: 1.0.0
 * Requires at least: 6.2
 * Requires PHP: 8.1
 * License: MIT
 */

defined('ABSPATH') || exit;
require_once __DIR__ . '/includes/class-hs-profile.php';
require_once __DIR__ . '/includes/class-hs-rest.php';

function hs_activate(): void {
    global $wpdb;
    require_once ABSPATH . 'wp-admin/includes/upgrade.php';
    $table = $wpdb->prefix . 'homily_studio_jobs';
    $collation = $wpdb->get_charset_collate();
    dbDelta("CREATE TABLE $table (
        id bigint(20) unsigned NOT NULL AUTO_INCREMENT,
        status varchar(16) NOT NULL DEFAULT 'pending',
        kind varchar(10) NOT NULL,
        label varchar(40) NOT NULL DEFAULT '',
        profile longtext NOT NULL,
        input longtext NOT NULL,
        result longtext NULL,
        error text NULL,
        claim varchar(64) NOT NULL DEFAULT '',
        worker bigint(20) unsigned NOT NULL DEFAULT 0,
        favourite tinyint(1) NOT NULL DEFAULT 0,
        notes text NULL,
        created datetime NOT NULL,
        claimed datetime NULL,
        finished datetime NULL,
        PRIMARY KEY  (id),
        KEY status_created (status,created)
    ) $collation;");
    add_role('homily_settings_reader', 'Homily Settings Reader', ['read' => true, 'read_homily_settings' => true]);
    add_role('homily_preview_worker', 'Homily Preview Worker', [
        'read' => true, 'read_homily_settings' => true, 'process_homily_tests' => true,
    ]);
    $admin = get_role('administrator');
    if ($admin) {
        $admin->add_cap('read_homily_settings');
        $admin->add_cap('process_homily_tests');
    }
    add_option('hs_active_profile', HS_Profile::defaults(), '', false);
    add_option('hs_draft_profile', HS_Profile::defaults(), '', false);
    if (!wp_next_scheduled('hs_cleanup')) {
        wp_schedule_event(time(), 'daily', 'hs_cleanup');
    }
}

register_activation_hook(__FILE__, 'hs_activate');
register_deactivation_hook(__FILE__, function (): void { wp_clear_scheduled_hook('hs_cleanup'); });
add_action('hs_cleanup', ['HS_REST', 'cleanup']);
add_action('rest_api_init', ['HS_REST', 'register']);
add_action('rest_api_init', function (): void {
    foreach (get_post_types(['show_in_rest' => true]) as $type) {
        register_rest_field($type, 'homily_source_filename', [
            'get_callback' => fn($object) => current_user_can('edit_post', $object['id']) ? get_post_meta($object['id'], '_homily_source_filename', true) : '',
            'update_callback' => function ($value, $post) {
                if (!current_user_can('edit_post', $post->ID)) { return new WP_Error('hs_forbidden', 'Cannot edit this post.', ['status' => 403]); }
                if (!is_string($value) || !preg_match('/^Mass-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.mp3$/', $value)) {
                    return new WP_Error('hs_filename', 'Invalid recording filename.', ['status' => 400]);
                }
                update_post_meta($post->ID, '_homily_source_filename', $value);
                return true;
            },
            'schema' => ['type' => 'string', 'context' => ['edit']],
        ]);
    }
});
add_filter('rest_post_dispatch', function ($response, $server, $request) {
    if (str_starts_with($request->get_route(), '/homily-studio/v1/')) {
        $response->header('Cache-Control', 'private, no-store, max-age=0');
        $response->header('Vary', 'Authorization, Cookie');
        $response->header('X-Content-Type-Options', 'nosniff');
    }
    return $response;
}, 10, 3);

add_action('admin_menu', function (): void {
    add_menu_page('Homily Studio', 'Homily Studio', 'manage_options', 'homily-studio', function (): void {
        if (!current_user_can('manage_options')) { return; }
        echo '<div class="wrap" id="homily-studio"><h1>Homily Studio</h1><p>Loading editorial workspace…</p></div>';
    }, 'dashicons-format-audio', 65);
});
add_action('admin_enqueue_scripts', function ($hook): void {
    if ($hook !== 'toplevel_page_homily-studio') { return; }
    wp_enqueue_style('homily-studio', plugins_url('assets/admin.css', __FILE__), [], '1.0.0');
    wp_enqueue_script('homily-studio', plugins_url('assets/admin.js', __FILE__), [], '1.0.0', true);
    wp_localize_script('homily-studio', 'homilyStudio', [
        'root' => esc_url_raw(rest_url('homily-studio/v1/')), 'nonce' => wp_create_nonce('wp_rest'),
        'recordingExample' => 'Mass-' . wp_date('Y-m-d') . '_09-00.mp3',
    ]);
});
