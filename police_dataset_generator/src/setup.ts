/**
 * npm run setup: create the database if missing, then apply sql/schema.sql.
 */
import fs from 'fs';
import path from 'path';
import { ROOT, dbSettings } from './lib/config';
import { connect } from './lib/store';

async function main() {
  const s = dbSettings();
  if (!/^[A-Za-z0-9_]+$/.test(s.database)) throw new Error(`DB_NAME "${s.database}" may only contain letters, digits and underscores`);

  console.log(`Connecting to MySQL at ${s.host}:${s.port} as ${s.user} ...`);
  const conn = await connect({ withDatabase: false, multipleStatements: true });
  try {
    await conn.query(`CREATE DATABASE IF NOT EXISTS \`${s.database}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`);
    await conn.query(`USE \`${s.database}\``);
    await conn.query(fs.readFileSync(path.join(ROOT, 'sql', 'schema.sql'), 'utf8'));

    const [rows] = await conn.query(
      `SELECT table_name AS t, table_rows AS n FROM information_schema.tables
       WHERE table_schema = ? AND table_name LIKE 'police%' ORDER BY table_name`,
      [s.database],
    );
    console.log(`Database "${s.database}" is ready. Police tables:`);
    for (const r of rows as { t: string }[]) console.log(`  - ${r.t}`);
  } finally {
    await conn.end();
  }
}

main().catch((e) => {
  console.error(`Setup failed: ${e.message}`);
  process.exit(1);
});
