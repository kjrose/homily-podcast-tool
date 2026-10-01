<?php
/** Offline REST contract tests. No WordPress site, network, credentials or paid API calls. */
define('ABSPATH', __DIR__ . '/');
define('DAY_IN_SECONDS', 86400);
define('ARRAY_A', 'ARRAY_A');
require_once dirname(__DIR__) . '/wordpress-plugin/homily-studio/includes/class-hs-profile.php';
require_once dirname(__DIR__) . '/wordpress-plugin/homily-studio/includes/class-hs-rest.php';

class WP_Error {
    public function __construct(public string $code, public string $message, public array $data = []) {}
}
class Request implements ArrayAccess {
    public function __construct(public array $params = [], public string $nonce = '') {}
    public function get_param($key) { return $this->params[$key] ?? null; }
    public function get_header($key) { return $key === 'X-WP-Nonce' ? $this->nonce : ''; }
    public function offsetExists(mixed $key): bool { return isset($this->params[$key]); }
    public function offsetGet(mixed $key): mixed { return $this->params[$key] ?? null; }
    public function offsetSet(mixed $key, mixed $value): void { $this->params[$key] = $value; }
    public function offsetUnset(mixed $key): void { unset($this->params[$key]); }
}
$options = []; $routes = []; $caps = []; $session = ''; $user = 0; $assertions = 0;
function current_user_can($cap, ...$args) { global $caps; return in_array($cap, $caps, true); }
function wp_get_session_token() { global $session; return $session; }
function wp_verify_nonce($nonce, $action) { return $nonce === 'test-nonce' && $action === 'wp_rest'; }
function get_current_user_id() { global $user; return $user; }
function wp_generate_uuid4() { static $id = 0; return 'test-revision-' . ++$id; }
function wp_generate_password($length, $special = true, $extra = false) { return str_repeat('c', $length); }
function is_wp_error($value) { return $value instanceof WP_Error; }
function wp_json_encode($value) { return json_encode($value, JSON_THROW_ON_ERROR); }
function absint($value) { return abs((int) $value); }
function get_option($key, $default = false) { global $options; return $options[$key] ?? $default; }
function add_option($key, $value, ...$args) { global $options; if (isset($options[$key])) return false; $options[$key] = $value; return true; }
function update_option($key, $value, ...$args) { global $options; $options[$key] = $value; return true; }
function delete_option($key) { global $options; unset($options[$key]); }
function wp_cache_delete(...$args) {}
function register_rest_route($namespace, $route, $args) { global $routes; $routes[] = [$route, $args]; }

class MemoryDatabase {
    public string $prefix = 'test_'; public string $options = 'test_options';
    public int $insert_id = 0; public array $rows = []; public bool $claim_race = false;
    public function prepare($sql, ...$args) { return 'PREPARED:' . json_encode([$sql, $args]); }
    private function decode($query) { return str_starts_with($query, 'PREPARED:') ? json_decode(substr($query, 9), true) : [$query, []]; }
    public function get_var($query) {
        [$sql, $args] = $this->decode($query);
        if (str_contains($sql, 'COUNT(*)')) return count($this->rows);
        foreach ($this->rows as $row) if ($row['status'] === 'pending') return $row['id'];
        return null;
    }
    public function get_row($query, $format) { [, $args] = $this->decode($query); return $this->rows[(int) $args[0]] ?? null; }
    public function get_results($query, $format) { return array_values($this->rows); }
    public function insert($table, $values) {
        $this->insert_id++;
        $this->rows[$this->insert_id] = array_replace(['id' => $this->insert_id, 'status' => 'pending',
            'worker' => 0, 'claim' => '', 'result' => null, 'error' => null, 'notes' => '', 'favourite' => 0], $values);
        return 1;
    }
    public function update($table, $values, $where) {
        $id = (int) $where['id'];
        if (!isset($this->rows[$id])) return 0;
        foreach ($where as $key => $value) if ($this->rows[$id][$key] != $value) return 0;
        $this->rows[$id] = array_replace($this->rows[$id], $values); return 1;
    }
    public function query($query) {
        [$sql, $args] = $this->decode($query);
        if (str_contains($sql, 'DELETE FROM test_options')) {
            global $options;
            if (($options[$args[0]] ?? null) === $args[1]) { unset($options[$args[0]]); return 1; }
            return 0;
        }
        if (str_contains($sql, "SET status = 'running'")) {
            [$claim, $worker, $claimed, $id] = $args;
            if ($this->claim_race || $this->rows[$id]['status'] !== 'pending') return 0;
            return $this->update('', ['status' => 'running', 'claim' => $claim, 'worker' => $worker, 'claimed' => $claimed], ['id' => $id]);
        }
        return 0;
    }
}
$wpdb = new MemoryDatabase();
function check($condition, $message): void { global $assertions; $assertions++; if (!$condition) throw new RuntimeException($message); }
function login($role, $browser = false): void {
    global $caps, $session, $user;
    $caps = match ($role) {
        'admin' => ['manage_options', 'read_homily_settings', 'process_homily_tests'],
        'reader' => ['read_homily_settings'], 'worker' => ['read_homily_settings', 'process_homily_tests'], default => [],
    };
    $session = $browser ? 'test-browser-session' : '';
    $user = $role === 'worker' ? 2 : ($role === 'admin' ? 1 : 3);
}
function dispatch($path, $method = 'GET', $params = [], $nonce = '') {
    global $routes;
    foreach ($routes as [$pattern, $route]) {
        if ($route['methods'] !== $method || !preg_match('#^' . $pattern . '$#', $path, $matches)) continue;
        foreach ($matches as $key => $value) if (is_string($key)) $params[$key] = $value;
        $request = new Request($params, $nonce);
        if (!call_user_func($route['permission_callback'], $request)) return new WP_Error('forbidden', 'Forbidden', ['status' => 403]);
        return call_user_func($route['callback'], $request);
    }
    return new WP_Error('no_route', 'No route', ['status' => 404]);
}

HS_REST::register();
$profile = HS_Profile::defaults();
check(!is_wp_error(HS_Profile::validate($profile)), 'Default profile must validate');
foreach ([['unknown' => 'example'], ['schema_version' => true], ['images_enabled' => 'false'], ['image_quality' => 'extreme'], ['style' => []]] as $changes) {
    check(is_wp_error(HS_Profile::validate(array_replace($profile, $changes))), 'Invalid profile must be rejected');
}
foreach (['{{password}}', '{{title', '{{title|execute}}'] as $template) {
    $bad = $profile; $bad['prompts']['title'] = $template;
    check(is_wp_error(HS_Profile::validate($bad)), 'Invalid template must be rejected');
}

foreach (['anonymous', 'subscriber'] as $role) {
    login($role);
    foreach ($routes as [$path, $route]) {
        $path = preg_replace('/\(\?P<id>\\d\+\)/', '1', $path);
        check(is_wp_error(dispatch($path, $route['methods'])), "$role cannot access $path");
    }
}
login('reader');
check(dispatch('/settings')['revision'] === 'local', 'Reader can read active settings');
foreach (['/draft', '/activate', '/restore', '/jobs', '/jobs/claim', '/jobs/1/result'] as $path) {
    check(is_wp_error(dispatch($path, 'POST')), 'Reader cannot write plugin state');
}
login('worker');
check(!is_wp_error(dispatch('/settings')), 'Worker can read settings');
foreach (['/workspace', '/jobs', '/jobs/1', '/jobs/1/image'] as $path) check(is_wp_error(dispatch($path)), 'Worker cannot view admin data');
foreach (['/draft', '/activate', '/restore', '/jobs', '/jobs/1/apply'] as $path) check(is_wp_error(dispatch($path, 'POST')), 'Worker cannot edit profiles or create tests');
check(is_wp_error(dispatch('/settings', 'POST')), 'Settings endpoint has no write method');
login('admin');
check(is_wp_error(dispatch('/draft', 'POST', [], 'test-nonce')), 'Admin Application Password has no browser edit access');
login('admin', true);
check(is_wp_error(dispatch('/draft', 'POST')), 'Admin edits require a nonce');
$draft = $profile; $draft['voice'] = 'Our site voice';
$saved = dispatch('/draft', 'POST', ['profile' => $draft, 'base_revision' => 'local'], 'test-nonce');
check(!is_wp_error($saved), 'Admin can save a draft');
check(dispatch('/settings')['voice'] !== 'Our site voice', 'Draft save does not activate');
check(is_wp_error(dispatch('/draft', 'POST', ['profile' => $draft, 'base_revision' => 'local'], 'test-nonce')), 'Stale save cannot overwrite draft');
check(is_wp_error(dispatch('/activate', 'POST', ['revision' => 'stale'], 'test-nonce')), 'Stale activation rejected');
dispatch('/activate', 'POST', ['revision' => $saved['revision']], 'test-nonce');
check(dispatch('/settings')['voice'] === 'Our site voice', 'Explicit activation changes active settings');
$restored = dispatch('/restore', 'POST', ['revision' => 'local'], 'test-nonce');
check($restored['voice'] !== 'Our site voice', 'History restores previous profile to draft');
check(dispatch('/settings')['voice'] === 'Our site voice', 'Restore leaves active settings unchanged');

$input = ['filename' => 'Mass-2026-10-01_09-00.mp3', 'transcript' => 'The homily source', 'title' => 'Hope', 'description' => 'A reflection.'];
$queued = dispatch('/jobs', 'POST', ['profile' => $draft, 'kind' => 'text', 'input' => $input, 'compare_active' => true], 'test-nonce');
check(count($queued['ids']) === 2, 'Comparison queues independent draft and active jobs');
check(is_wp_error(dispatch('/jobs', 'POST', ['profile' => $draft, 'kind' => 'text', 'input' => []], 'test-nonce')), 'Text previews need source context');
$disabled = $draft; $disabled['images_enabled'] = false;
check(is_wp_error(dispatch('/jobs', 'POST', ['profile' => $disabled, 'kind' => 'image', 'input' => $input], 'test-nonce')), 'Disabled image renders rejected');
login('worker');
$wpdb->claim_race = true;
check(dispatch('/jobs/claim', 'POST') === null, 'Concurrent claim loser cannot process the same job');
$wpdb->claim_race = false;
$claimed = dispatch('/jobs/claim', 'POST');
check($claimed['id'] === $queued['ids'][0], 'Worker claims oldest pending job');
$next = dispatch('/jobs/claim', 'POST');
check($next['id'] !== $claimed['id'], 'A claimed job cannot be claimed again');
check(is_wp_error(dispatch('/jobs/' . $claimed['id'] . '/result', 'POST', ['claim' => 'wrong', 'result' => []])), 'Claim token required for result delivery');
$payload = ['claim' => $claimed['claim'], 'result' => ['metadata' => ['title' => 'Hope', 'description' => 'A reflection.']]];
$user = 99;
check(is_wp_error(dispatch('/jobs/' . $claimed['id'] . '/result', 'POST', $payload)), 'Claims belong to the worker user');
$user = 2;
check(dispatch('/jobs/' . $claimed['id'] . '/result', 'POST', $payload)['stored'], 'Worker delivers its claimed result');
check(dispatch('/jobs/' . $claimed['id'] . '/result', 'POST', $payload)['stored'], 'Result delivery is idempotent');
check(dispatch('/settings')['voice'] === 'Our site voice', 'Result delivery cannot change active settings');

login('admin', true);
$concept = dispatch('/jobs', 'POST', ['profile' => $draft, 'kind' => 'concept', 'input' => $input], 'test-nonce');
login('worker');
$job = dispatch('/jobs/claim', 'POST');
dispatch('/jobs/' . $job['id'] . '/result', 'POST', ['claim' => $job['claim'], 'result' => ['image_prompt' => 'Reviewed woodcut image prompt', 'input' => $input]]);
login('admin', true);
$render = dispatch('/jobs', 'POST', ['concept_job_id' => $concept['ids'][0]], 'test-nonce');
check(!is_wp_error($render), 'Reviewed concept can be queued for rendering');
login('worker');
$job = dispatch('/jobs/claim', 'POST');
check($job['input']['reviewed_image_prompt'] === 'Reviewed woodcut image prompt', 'Reviewed final prompt retained without new concept generation');
check(is_wp_error(dispatch('/jobs/' . $job['id'] . '/result', 'POST', ['claim' => $job['claim'], 'result' => ['image_base64' => base64_encode('not an image')]])), 'Invalid image rejected');
$chunk = fn($type, $data) => pack('N', strlen($data)) . $type . $data . pack('N', crc32($type . $data));
$png = "\x89PNG\r\n\x1a\n" . $chunk('IHDR', pack('NNCCCCC', 1024, 1024, 8, 2, 0, 0, 0)) .
    $chunk('IDAT', gzcompress(str_repeat("\0" . str_repeat("\0", 3072), 1024))) . $chunk('IEND', '');
check(dispatch('/jobs/' . $job['id'] . '/result', 'POST', ['claim' => $job['claim'], 'result' => ['image_base64' => base64_encode($png)]])['stored'], 'Valid square PNG accepted');
check(is_wp_error(dispatch('/jobs/' . $job['id'] . '/image')), 'Worker cannot retrieve stored private image');
login('admin', true);
check(dispatch('/jobs/' . $job['id'] . '/image', 'GET', [], 'test-nonce')['image_base64'] === base64_encode($png), 'Admin can view private image');
$detail = dispatch('/jobs/' . $job['id'], 'GET', [], 'test-nonce');
check($detail['has_image'] && !isset($detail['result']['image_base64']) && !isset($detail['claim']), 'Job details exclude image bytes and claim credentials');

while (count($wpdb->rows) < 20) dispatch('/jobs', 'POST', ['profile' => $draft, 'kind' => 'text', 'input' => $input], 'test-nonce');
check(is_wp_error(dispatch('/jobs', 'POST', ['profile' => $draft, 'kind' => 'text', 'input' => $input], 'test-nonce')), 'Daily quota enforced on server');
echo "WordPress plugin contract: $assertions assertions passed.\n";
