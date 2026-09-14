import fs from 'node:fs';
import path from 'node:path';
import cors from '@fastify/cors';
import fastifyStatic from '@fastify/static';
import Fastify from 'fastify';
import { config, log } from './config.ts';
import { count, db } from './db/index.ts';
import { migrate } from './db/migrate.ts';
import { llm } from './llm/index.ts';
import { registerRoutes } from './routes/index.ts';

const WEB_DIST = path.join(config.root, 'packages/web/dist');

async function main() {
  migrate(db());
  llm();

  const app = Fastify({ logger: false, bodyLimit: 8 * 1024 * 1024 });
  await app.register(cors, { origin: true });
  await registerRoutes(app);

  if (fs.existsSync(path.join(WEB_DIST, 'index.html'))) {
    await app.register(fastifyStatic, { root: WEB_DIST, prefix: '/' });
    // The interface is a single page; anything that is not an API route is it.
    app.setNotFoundHandler((request, reply) => {
      if (request.url.startsWith('/api/')) return reply.code(404).send({ error: 'not found' });
      return reply.type('text/html').send(fs.readFileSync(path.join(WEB_DIST, 'index.html')));
    });
  } else {
    app.setNotFoundHandler((request, reply) => {
      if (request.url.startsWith('/api/')) return reply.code(404).send({ error: 'not found' });
      return reply
        .type('text/html')
        .send(
          '<pre style="font:14px ui-monospace,monospace;padding:32px;line-height:1.6">' +
            'The interface has not been built yet.\n\n' +
            'Run  npm run build   then reload, or\n' +
            'run  npm run dev     for the development server on port ' +
            config.webPort +
            '.</pre>',
        );
    });
  }

  await app.listen({ port: config.port, host: '0.0.0.0' });

  const dictations = count('SELECT COUNT(*) AS n FROM dictations WHERE user_id = ?', [config.defaultUserId]);
  const memories = count(
    `SELECT COUNT(*) AS n FROM memories WHERE user_id = ? AND status = 'active'`,
    [config.defaultUserId],
  );

  log('info', '');
  log('info', `  Kivi  http://localhost:${config.port}`);
  log('info', `        ${dictations} dictations · ${memories} active memories · provider ${llm().name}`);
  if (dictations === 0) log('info', '        no history yet — run `npm run seed`');
  log('info', '');
}

main().catch((err) => {
  console.error('!     server failed to start:', err);
  process.exit(1);
});
