import pino from 'pino';

const level = process.env.LOG_LEVEL ?? 'info';

const usePretty = process.env.NODE_ENV !== 'production' && process.env.LOG_JSON !== '1';

export const logger = pino(
  usePretty
    ? {
        level,
        transport: {
          target: 'pino-pretty',
          options: { colorize: true, translateTime: 'HH:MM:ss.l', ignore: 'pid,hostname' },
        },
      }
    : { level },
);

export type Logger = typeof logger;
