<?php
/**
 * JARVIS_BLOG_PUBLISHER_V1 — pont de lecture/ecriture vers le blog du site.
 *
 * Ce fichier n'est PAS un CMS parallele : il appelle le systeme reel du site
 * (config/db.php + includes_app/blog_content.php, tables blog_posts /
 * blog_categories). Il est deploye par JARVIS et invoque en CLI uniquement.
 *
 * Usage : php tools/jarvis_blog_bridge.php <payload-json-base64>
 * Sortie : une seule ligne JSON {"ok":bool,...}. Rien d'autre.
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    exit('CLI only');
}

$root = dirname(__DIR__);

function jb_out(array $data): void
{
    echo json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), "\n";
    exit(0);
}

function jb_fail(string $code, string $detail = ''): void
{
    jb_out(['ok' => false, 'error' => $code, 'detail' => $detail]);
}

$raw = $argv[1] ?? '';
$json = $raw === '' ? '' : (string) base64_decode($raw, true);
$payload = $json === '' ? null : json_decode($json, true);
if (!is_array($payload)) {
    jb_fail('BAD_PAYLOAD');
}
$op = (string) ($payload['op'] ?? '');

require_once $root . '/includes_app/blog_content.php';
require_once $root . '/config/db.php';

$pdo = function_exists('blog_db') ? blog_db() : null;
if (!$pdo instanceof PDO) {
    jb_fail('NO_DB');
}
$pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

/** Colonnes reellement presentes : on n'ecrit jamais une colonne inexistante. */
function jb_columns(PDO $pdo, string $table): array
{
    $cols = [];
    foreach ($pdo->query('SHOW COLUMNS FROM `' . str_replace('`', '``', $table) . '`') as $row) {
        $cols[(string) $row['Field']] = true;
    }
    return $cols;
}

$POST_FIELDS = ['category_id', 'title', 'slug', 'status', 'cover_image', 'excerpt',
                'content', 'meta_title', 'meta_desc', 'author_id', 'published_at'];
$NULLABLE = ['category_id', 'author_id', 'published_at', 'cover_image'];

try {
    switch ($op) {
        case 'ping':
            jb_out(['ok' => true, 'php' => PHP_VERSION,
                    'columns' => array_keys(jb_columns($pdo, 'blog_posts'))]);

        case 'categories':
            $rows = $pdo->query('SELECT id, name, slug FROM blog_categories ORDER BY id')
                        ->fetchAll(PDO::FETCH_ASSOC);
            jb_out(['ok' => true, 'categories' => $rows]);

        case 'posts':
            $limit = max(1, min(200, (int) ($payload['limit'] ?? 50)));
            $st = $pdo->query(
                'SELECT id, category_id, title, slug, status, cover_image, excerpt, content,
                        meta_title, meta_desc, published_at, created_at, updated_at
                 FROM blog_posts ORDER BY id DESC LIMIT ' . $limit);
            jb_out(['ok' => true, 'posts' => $st->fetchAll(PDO::FETCH_ASSOC)]);

        case 'get':
            $slug = trim((string) ($payload['slug'] ?? ''));
            $id = (int) ($payload['id'] ?? 0);
            if ($slug === '' && $id <= 0) {
                jb_fail('MISSING_TARGET');
            }
            $st = $slug !== ''
                ? $pdo->prepare('SELECT * FROM blog_posts WHERE slug = ? LIMIT 1')
                : $pdo->prepare('SELECT * FROM blog_posts WHERE id = ? LIMIT 1');
            $st->execute([$slug !== '' ? $slug : $id]);
            $row = $st->fetch(PDO::FETCH_ASSOC);
            jb_out(['ok' => true, 'post' => $row ?: null]);

        case 'search':
            $terms = $payload['terms'] ?? [];
            if (!is_array($terms) || !$terms) {
                jb_fail('MISSING_TERMS');
            }
            $where = [];
            $args = [];
            foreach (array_slice($terms, 0, 12) as $t) {
                $t = trim((string) $t);
                if ($t === '') {
                    continue;
                }
                $where[] = '(title LIKE ? OR slug LIKE ?)';
                $args[] = '%' . $t . '%';
                $args[] = '%' . $t . '%';
            }
            if (!$where) {
                jb_fail('MISSING_TERMS');
            }
            $st = $pdo->prepare(
                'SELECT id, title, slug, status, published_at, updated_at FROM blog_posts WHERE '
                . implode(' OR ', $where) . ' ORDER BY id DESC LIMIT 25');
            $st->execute($args);
            jb_out(['ok' => true, 'matches' => $st->fetchAll(PDO::FETCH_ASSOC)]);

        case 'save':
            $fields = $payload['fields'] ?? [];
            if (!is_array($fields)) {
                jb_fail('BAD_FIELDS');
            }
            $cols = jb_columns($pdo, 'blog_posts');
            $id = (int) ($payload['id'] ?? 0);
            $writable = [];
            foreach ($POST_FIELDS as $f) {
                if (array_key_exists($f, $fields) && isset($cols[$f])) {
                    $v = $fields[$f];
                    $writable[$f] = ($v === '' && in_array($f, $NULLABLE, true)) ? null : $v;
                }
            }
            if (!$writable) {
                jb_fail('NOTHING_TO_WRITE');
            }
            if ($id > 0) {
                $set = [];
                $args = [];
                foreach ($writable as $f => $v) {
                    $set[] = '`' . $f . '` = ?';
                    $args[] = $v;
                }
                if (isset($cols['updated_at'])) {
                    $set[] = '`updated_at` = NOW()';
                }
                $args[] = $id;
                $pdo->prepare('UPDATE blog_posts SET ' . implode(', ', $set) . ' WHERE id = ?')
                    ->execute($args);
                $savedId = $id;
                $action = 'updated';
            } else {
                $names = [];
                $marks = [];
                $args = [];
                foreach ($writable as $f => $v) {
                    $names[] = '`' . $f . '`';
                    $marks[] = '?';
                    $args[] = $v;
                }
                $pdo->prepare('INSERT INTO blog_posts (' . implode(', ', $names) . ') VALUES ('
                              . implode(', ', $marks) . ')')->execute($args);
                $savedId = (int) $pdo->lastInsertId();
                $action = 'created';
            }
            $st = $pdo->prepare('SELECT id, title, slug, status, published_at FROM blog_posts WHERE id = ?');
            $st->execute([$savedId]);
            jb_out(['ok' => true, 'action' => $action, 'post' => $st->fetch(PDO::FETCH_ASSOC)]);

        case 'publish':
            $id = (int) ($payload['id'] ?? 0);
            if ($id <= 0) {
                jb_fail('MISSING_ID');
            }
            $when = trim((string) ($payload['published_at'] ?? ''));
            $st = $pdo->prepare(
                "UPDATE blog_posts SET status = 'published',
                 published_at = COALESCE(NULLIF(?, ''), published_at, NOW()) WHERE id = ?");
            $st->execute([$when, $id]);
            $g = $pdo->prepare('SELECT id, title, slug, status, published_at FROM blog_posts WHERE id = ?');
            $g->execute([$id]);
            jb_out(['ok' => true, 'post' => $g->fetch(PDO::FETCH_ASSOC)]);

        default:
            jb_fail('UNKNOWN_OP', $op);
    }
} catch (Throwable $e) {
    jb_fail('DB_ERROR', substr($e->getMessage(), 0, 400));
}
