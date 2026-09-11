import 'dotenv/config';
import { getConfig } from './config.js';
import { logger } from './logger.js';
import { startServer } from './server.js';

async function main(): Promise<void> {
  const config = getConfig();
  const server = await startServer(config, logger);

  const shutdown = (signal: string) => {
    logger.info({ signal }, 'Shutting down');
    server
      .close()
      .then(() => process.exit(0))
      .catch(() => process.exit(1));
  };

  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

main().catch((err) => {
  logger.error({ err }, 'Fatal error during startup');
  process.exit(1);
});
