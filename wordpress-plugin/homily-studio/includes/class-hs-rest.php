<?php
defined('ABSPATH') || exit;

final class HS_REST {
    private static function table(): string { global $wpdb; return $wpdb->prefix . 'homily_studio_jobs'; }

    public static function admin($request): bool {
        // Browser session plus capability plus nonce. Application Passwords cannot edit profiles.
        return current_user_can('manage_options') && wp_get_session_token() !== '' &&
            (bool) wp_verify_nonce($request->get_header('X-WP-Nonce'), 'wp_rest');
    }
    public static function reader(): bool { return current_user_can('read_homily_settings'); }
    public static function worker(): bool { return current_user_can('process_homily_tests'); }

    public static function register(): void {
        $routes = [
            ['/settings', 'GET', 'settings', 'reader'],
            ['/workspace', 'GET', 'workspace', 'admin'],
            ['/draft', 'POST', 'save_draft', 'admin'],
            ['/activate', 'POST', 'activate', 'admin'],
            ['/restore', 'POST', 'restore', 'admin'],
            ['/jobs', 'GET', 'jobs', 'admin'],
            ['/jobs', 'POST', 'queue', 'admin'],
            ['/jobs/claim', 'POST', 'claim', 'worker'],
            ['/jobs/(?P<id>\d+)', 'GET', 'job', 'admin'],
            ['/jobs/(?P<id>\d+)', 'POST', 'annotate', 'admin'],
            ['/jobs/(?P<id>\d+)/image', 'GET', 'image', 'admin'],
            ['/jobs/(?P<id>\d+)/result', 'POST', 'result', 'worker'],
            ['/jobs/(?P<id>\d+)/apply', 'POST', 'apply_image', 'admin'],
            ['/sources', 'GET', 'sources', 'admin'],
        ];
        foreach ($routes as [$path, $method, $callback, $permission]) {
            register_rest_route('homily-studio/v1', $path, [
                'methods' => $method, 'callback' => [self::class, $callback],
                'permission_callback' => [self::class, $permission],
            ]);
        }
    }

    public static function settings(): array { return get_option('hs_active_profile', HS_Profile::defaults()); }
    public static function workspace(): array {
        return ['active' => self::settings(), 'draft' => get_option('hs_draft_profile', HS_Profile::defaults()),
                'history' => get_option('hs_profile_history', []), 'catalog' => HS_Profile::catalog(),
                'daily_limit' => 20, 'retention_days' => 7];
    }
    public static function save_draft($request) {
        $profile = HS_Profile::validate($request->get_param('profile'));
        if (is_wp_error($profile)) { return $profile; }
        return HS_Profile::lock(function () use ($profile, $request) {
            $current = get_option('hs_draft_profile', HS_Profile::defaults());
            if ($request->get_param('base_revision') !== $current['revision']) {
                return new WP_Error('hs_conflict', 'The draft changed in another session. Reload before saving.', ['status' => 409]);
            }
            $saved = HS_Profile::stamped($profile);
            update_option('hs_draft_profile', $saved, false);
            return $saved;
        });
    }
    public static function activate($request) {
        return HS_Profile::lock(function () use ($request) {
            $draft = get_option('hs_draft_profile', HS_Profile::defaults());
            if ($request->get_param('revision') !== $draft['revision']) {
                return new WP_Error('hs_conflict', 'Save and reload the current draft before activation.', ['status' => 409]);
            }
            $history = get_option('hs_profile_history', []);
            array_unshift($history, ['profile' => self::settings(), 'changed_at' => gmdate('c'), 'changed_by' => get_current_user_id()]);
            update_option('hs_profile_history', array_slice($history, 0, 25), false);
            update_option('hs_active_profile', $draft, false);
            return $draft;
        });
    }
    public static function restore($request) {
        return HS_Profile::lock(function () use ($request) {
            foreach (get_option('hs_profile_history', []) as $item) {
                if ($item['profile']['revision'] === $request->get_param('revision')) {
                    $draft = HS_Profile::stamped($item['profile']);
                    update_option('hs_draft_profile', $draft, false);
                    return $draft;
                }
            }
            return new WP_Error('hs_missing', 'Revision not found.', ['status' => 404]);
        });
    }

    public static function cleanup(): void {
        global $wpdb;
        $table = self::table();
        $wpdb->query($wpdb->prepare("DELETE FROM $table WHERE created < %s AND favourite = 0 AND status <> 'running'", gmdate('Y-m-d H:i:s', time() - 7 * DAY_IN_SECONDS)));
        // Never silently requeue an interrupted paid generation.
        $wpdb->query($wpdb->prepare("UPDATE $table SET status = 'failed', error = %s WHERE status = 'running' AND claimed < %s",
            'Worker interrupted or timed out. Queue a new test explicitly if needed.', gmdate('Y-m-d H:i:s', time() - 1800)));
    }

    public static function queue($request) {
        $reviewed = null;
        if ($request->get_param('concept_job_id') !== null) {
            $reviewed = self::find(absint($request->get_param('concept_job_id')));
            $review_result = json_decode($reviewed['result'] ?? 'null', true);
            if (!$reviewed || $reviewed['status'] !== 'complete' || $reviewed['kind'] !== 'concept' ||
                !isset($review_result['image_prompt']) || !is_string($review_result['image_prompt'])) {
                return new WP_Error('hs_concept', 'Choose a completed concept preview.', ['status' => 400]);
            }
        }
        $profile = HS_Profile::validate($reviewed ? json_decode($reviewed['profile'], true) : $request->get_param('profile'));
        if (is_wp_error($profile)) { return $profile; }
        $kind = $reviewed ? 'image' : $request->get_param('kind');
        if (!in_array($kind, ['text', 'concept', 'image', 'full'], true)) {
            return new WP_Error('hs_kind', 'Invalid preview kind.', ['status' => 400]);
        }
        $input = $reviewed ? ($review_result['input'] ?? json_decode($reviewed['input'], true)) : $request->get_param('input');
        if ($reviewed && is_array($input)) { unset($input['use_local_transcript'], $input['reviewed_image_prompt']); }
        if (!is_array($input) || array_diff(array_keys($input), ['title', 'description', 'transcript', 'filename'])) {
            return new WP_Error('hs_input', 'Invalid preview input.', ['status' => 400]);
        }
        $input = array_replace(['title' => '', 'description' => '', 'transcript' => '', 'filename' => ''], $input);
        foreach ($input as $key => $value) {
            if (!is_string($value) || strlen($value) > ($key === 'transcript' ? 60000 : 10000)) {
                return new WP_Error('hs_input', 'Preview input is too long or has an invalid type.', ['status' => 400]);
            }
        }
        $local = !$reviewed && $request->get_param('use_local_transcript') === true;
        if (in_array($kind, ['text', 'full'], true) && ((!trim($input['transcript']) && !$local) ||
            !preg_match('/^Mass-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.mp3$/', $input['filename']))) {
            return new WP_Error('hs_input', 'Text previews require a transcript and a Mass-YYYY-MM-DD_HH-MM.mp3 recording filename.', ['status' => 400]);
        }
        if ($local && !preg_match('/^Mass-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}\.mp3$/', $input['filename'])) {
            return new WP_Error('hs_input', 'Choose a mapped recording filename for local context.', ['status' => 400]);
        }
        if (in_array($kind, ['concept', 'image'], true) && !trim($input['title']) && !trim($input['transcript'])) {
            return new WP_Error('hs_input', 'Provide a title or a transcript for the image concept.', ['status' => 400]);
        }
        if (in_array($kind, ['image', 'full'], true) && !$profile['images_enabled']) {
            return new WP_Error('hs_input', 'Enable images in the draft before rendering.', ['status' => 400]);
        }
        if ($local) { $input['use_local_transcript'] = true; }
        if ($reviewed) { $input['reviewed_image_prompt'] = $review_result['image_prompt']; }
        return HS_Profile::lock(function () use ($profile, $input, $kind, $request) {
            global $wpdb;
            self::cleanup();
            $table = self::table();
            $count = (int) $wpdb->get_var($wpdb->prepare("SELECT COUNT(*) FROM $table WHERE created >= %s", gmdate('Y-m-d 00:00:00')));
            $profiles = [['label' => 'Draft', 'profile' => HS_Profile::stamped($profile)]];
            if ($request->get_param('compare_active') === true) {
                if (in_array($kind, ['image', 'full'], true) && !self::settings()['images_enabled']) {
                    return new WP_Error('hs_input', 'The active profile has images disabled; compare text or concepts instead.', ['status' => 400]);
                }
                $profiles[] = ['label' => 'Active', 'profile' => self::settings()];
            }
            if ($count + count($profiles) > 20) {
                return new WP_Error('hs_limit', 'Daily limit of 20 preview jobs reached (UTC day).', ['status' => 429]);
            }
            $ids = [];
            foreach ($profiles as $entry) {
                $ok = $wpdb->insert($table, ['kind' => $kind, 'label' => $entry['label'],
                    'profile' => wp_json_encode($entry['profile']), 'input' => wp_json_encode($input),
                    'created' => gmdate('Y-m-d H:i:s')]);
                if (!$ok) { return new WP_Error('hs_storage', 'Could not save preview job.', ['status' => 500]); }
                $ids[] = $wpdb->insert_id;
            }
            return ['ids' => $ids];
        });
    }

    public static function claim() {
        global $wpdb;
        self::cleanup();
        $table = self::table();
        $id = $wpdb->get_var("SELECT id FROM $table WHERE status = 'pending' ORDER BY id ASC LIMIT 1");
        if (!$id) { return null; }
        $token = wp_generate_password(48, false, false);
        $changed = $wpdb->query($wpdb->prepare("UPDATE $table SET status = 'running', claim = %s, worker = %d, claimed = %s WHERE id = %d AND status = 'pending'",
            $token, get_current_user_id(), gmdate('Y-m-d H:i:s'), $id));
        if ($changed !== 1) { return null; }
        $row = $wpdb->get_row($wpdb->prepare("SELECT * FROM $table WHERE id = %d", $id), ARRAY_A);
        return ['id' => (int) $id, 'claim' => $token, 'kind' => $row['kind'],
                'profile' => json_decode($row['profile'], true), 'input' => json_decode($row['input'], true)];
    }

    private static function find($id) {
        global $wpdb;
        $table = self::table();
        return $wpdb->get_row($wpdb->prepare("SELECT * FROM $table WHERE id = %d", $id), ARRAY_A);
    }

    public static function result($request) {
        global $wpdb;
        $row = self::find($request['id']);
        if (!$row || (int) $row['worker'] !== get_current_user_id() ||
            !is_string($request->get_param('claim')) || !hash_equals($row['claim'], $request->get_param('claim'))) {
            return new WP_Error('hs_claim', 'Invalid preview claim.', ['status' => 403]);
        }
        if (in_array($row['status'], ['complete', 'failed'], true)) { return ['stored' => true]; }
        if ($row['status'] !== 'running') { return new WP_Error('hs_state', 'Job is not running.', ['status' => 409]); }
        $result = $request->get_param('result');
        $error = $request->get_param('error');
        if ($error !== null) {
            if (!is_string($error) || strlen($error) > 1000) { return new WP_Error('hs_result', 'Invalid error.', ['status' => 400]); }
            $result = null;
        } else {
            if (!is_array($result) || strlen(wp_json_encode($result)) > 16000000) {
                return new WP_Error('hs_result', 'Invalid preview result.', ['status' => 400]);
            }
            if (isset($result['image_base64'])) {
                $png = self::png($result['image_base64']);
                if (is_wp_error($png)) { return $png; }
            }
        }
        $changed = $wpdb->update(self::table(), ['status' => $error !== null ? 'failed' : 'complete',
            'result' => $result === null ? null : wp_json_encode($result), 'error' => $error,
            'finished' => gmdate('Y-m-d H:i:s')], ['id' => (int) $row['id'], 'status' => 'running']);
        if ($changed === false) { return new WP_Error('hs_storage', 'Could not save result.', ['status' => 500]); }
        return ['stored' => true];
    }

    private static function png($encoded) {
        if (!is_string($encoded) || strlen($encoded) > 15000000) {
            return new WP_Error('hs_image', 'Invalid image payload.', ['status' => 400]);
        }
        $bytes = base64_decode($encoded, true);
        $info = $bytes === false ? false : @getimagesizefromstring($bytes);
        if (!$info || $info[2] !== IMAGETYPE_PNG || $info[0] !== 1024 || $info[1] !== 1024) {
            return new WP_Error('hs_image', 'Preview must be a 1024 × 1024 PNG.', ['status' => 400]);
        }
        return $bytes;
    }

    public static function jobs(): array {
        global $wpdb;
        $table = self::table();
        return $wpdb->get_results("SELECT id,status,kind,label,created,error,favourite,notes FROM $table ORDER BY id DESC LIMIT 50", ARRAY_A);
    }
    public static function job($request) {
        $row = self::find($request['id']);
        if (!$row) { return new WP_Error('hs_missing', 'Job not found.', ['status' => 404]); }
        $result = json_decode($row['result'] ?? 'null', true);
        $has_image = isset($result['image_base64']);
        unset($result['image_base64'], $row['claim'], $row['worker']);
        $row['result'] = $result;
        $row['has_image'] = $has_image;
        $row['profile'] = json_decode($row['profile'], true);
        $row['input'] = json_decode($row['input'], true);
        return $row;
    }
    public static function image($request) {
        $row = self::find($request['id']);
        $result = json_decode($row['result'] ?? 'null', true);
        if (!$row || !isset($result['image_base64'])) { return new WP_Error('hs_missing', 'Image not found.', ['status' => 404]); }
        return ['image_base64' => $result['image_base64']];
    }
    public static function annotate($request) {
        global $wpdb;
        $row = self::find($request['id']);
        if (!$row) { return new WP_Error('hs_missing', 'Job not found.', ['status' => 404]); }
        $notes = $request->get_param('notes');
        $favourite = $request->get_param('favourite');
        if (!is_string($notes) || strlen($notes) > 5000 || !is_bool($favourite)) {
            return new WP_Error('hs_input', 'Invalid notes or favourite.', ['status' => 400]);
        }
        $wpdb->update(self::table(), ['notes' => $notes, 'favourite' => $favourite ? 1 : 0], ['id' => $row['id']]);
        return ['saved' => true];
    }

    public static function sources(): array {
        $types = array_values(get_post_types(['show_in_rest' => true]));
        $types = array_diff($types, ['attachment', 'wp_block', 'wp_navigation', 'wp_template', 'wp_template_part']);
        $posts = get_posts(['post_type' => array_values($types), 'post_status' => ['draft', 'publish', 'pending', 'private'], 'numberposts' => 30]);
        return array_map(function ($post) {
            return ['id' => $post->ID, 'title' => html_entity_decode(wp_strip_all_tags($post->post_title), ENT_QUOTES, 'UTF-8'),
                'description' => wp_strip_all_tags($post->post_excerpt ?: $post->post_content),
                'filename' => get_post_meta($post->ID, '_homily_source_filename', true) ?: '',
                'status' => $post->post_status, 'type' => $post->post_type];
        }, array_values(array_filter($posts, fn($post) => current_user_can('edit_post', $post->ID))));
    }

    public static function apply_image($request) {
        $fields = [];
        foreach (['cover_url_field' => 'cover_image', 'cover_id_field' => 'cover_image_id'] as $key => $default) {
            $value = $request->get_param($key) ?? $default;
            if (!is_string($value) || (strlen($value) && !preg_match('/^[a-zA-Z0-9_-]{1,100}$/', $value))) {
                return new WP_Error('hs_field', 'Invalid cover metadata key.', ['status' => 400]);
            }
            $fields[$key] = $value;
        }
        $post_id = absint($request->get_param('post_id'));
        $post = get_post($post_id);
        if (!$post || $post->post_status !== 'draft' || !current_user_can('edit_post', $post_id) || !current_user_can('upload_files')) {
            return new WP_Error('hs_post', 'Choose an editable draft post.', ['status' => 403]);
        }
        $row = self::find($request['id']);
        $result = json_decode($row['result'] ?? 'null', true);
        if (!$row || !isset($result['image_base64'])) { return new WP_Error('hs_missing', 'Image not found.', ['status' => 404]); }
        $png = self::png($result['image_base64']);
        if (is_wp_error($png)) { return $png; }
        $file = wp_upload_bits('homily-preview-' . $row['id'] . '.png', null, $png);
        if ($file['error']) { return new WP_Error('hs_upload', 'Could not copy image to the media library.', ['status' => 500]); }
        require_once ABSPATH . 'wp-admin/includes/image.php';
        $id = wp_insert_attachment(['post_mime_type' => 'image/png', 'post_title' => 'Homily cover', 'post_status' => 'inherit'], $file['file'], $post_id, true);
        if (is_wp_error($id)) { wp_delete_file($file['file']); return $id; }
        wp_update_attachment_metadata($id, wp_generate_attachment_metadata($id, $file['file']));
        set_post_thumbnail($post_id, $id);
        if ($fields['cover_url_field']) { update_post_meta($post_id, $fields['cover_url_field'], wp_get_attachment_url($id)); }
        if ($fields['cover_id_field']) { update_post_meta($post_id, $fields['cover_id_field'], (string) $id); }
        return ['media_id' => $id];
    }
}
