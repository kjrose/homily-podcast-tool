<?php
defined('ABSPATH') || exit;

final class HS_Profile {
    public static function catalog(): array {
        static $catalog;
        if (!$catalog) {
            $catalog = json_decode(file_get_contents(dirname(__DIR__) . '/editorial-catalog.json'), true, 512, JSON_THROW_ON_ERROR);
        }
        return $catalog;
    }

    public static function defaults(): array { return self::catalog()['default_profile']; }

    public static function validate($value) {
        $defaults = self::defaults();
        if (!is_array($value) || array_diff(array_keys($value), array_keys($defaults))) {
            return new WP_Error('hs_profile', 'Unknown editorial profile fields.', ['status' => 400]);
        }
        $profile = array_replace($defaults, $value);
        if ($profile['schema_version'] !== 1 || !is_bool($profile['images_enabled']) ||
            !in_array($profile['image_quality'], ['low', 'medium', 'high', 'auto'], true) ||
            !is_string($profile['style']) || !isset(self::catalog()['styles'][$profile['style']])) {
            return new WP_Error('hs_profile', 'Invalid schema, style, or image settings.', ['status' => 400]);
        }
        foreach (['revision', 'site_name', 'voice', 'audience', 'language', 'style', 'palette', 'mood'] as $field) {
            if (!is_string($profile[$field]) || strlen($profile[$field]) > 10000) {
                return new WP_Error('hs_profile', 'Invalid profile text field.', ['status' => 400]);
            }
        }
        if (!is_array($profile['prompts']) ||
            array_diff(array_keys($profile['prompts']), array_keys($defaults['prompts'])) ||
            array_diff(array_keys($defaults['prompts']), array_keys($profile['prompts']))) {
            return new WP_Error('hs_profile', 'All supported prompt templates are required.', ['status' => 400]);
        }
        $allowed = ['site_name', 'voice', 'audience', 'language', 'title', 'description', 'transcript',
                    'filename', 'homilist', 'style', 'palette', 'mood'];
        foreach ($profile['prompts'] as $template) {
            if (!is_string($template) || !trim($template) || strlen($template) > 20000) {
                return new WP_Error('hs_template', 'Templates must be non-empty and at most 20,000 bytes.', ['status' => 400]);
            }
            preg_match_all('/\{\{\s*([a-z_]+)\s*\}\}/', $template, $tokens);
            $remaining = preg_replace('/\{\{\s*([a-z_]+)\s*\}\}/', '', $template);
            if (array_diff($tokens[1], $allowed) || str_contains($remaining, '{{') || str_contains($remaining, '}}')) {
                return new WP_Error('hs_template', 'Unknown or malformed template placeholder.', ['status' => 400]);
            }
        }
        return $profile;
    }

    public static function stamped(array $profile): array {
        $profile['revision'] = wp_generate_uuid4();
        return $profile;
    }

    public static function lock(callable $operation) {
        global $wpdb;
        // add_option's unique key gives cross-request exclusion. Compare-and-delete expires a crashed owner.
        $old = get_option('hs_write_lock');
        if (is_string($old) && (int) explode(':', $old)[0] < time() - 60) {
            $wpdb->query($wpdb->prepare("DELETE FROM {$wpdb->options} WHERE option_name = %s AND option_value = %s", 'hs_write_lock', $old));
            wp_cache_delete('hs_write_lock', 'options');
        }
        $owner = time() . ':' . wp_generate_uuid4();
        if (!add_option('hs_write_lock', $owner, '', false)) {
            return new WP_Error('hs_busy', 'Another update is in progress. Please retry.', ['status' => 409]);
        }
        try { return $operation(); }
        finally {
            $wpdb->query($wpdb->prepare("DELETE FROM {$wpdb->options} WHERE option_name = %s AND option_value = %s", 'hs_write_lock', $owner));
            wp_cache_delete('hs_write_lock', 'options');
        }
    }
}
