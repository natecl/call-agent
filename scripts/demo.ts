import 'dotenv/config';
import { loadConfig } from '../src/config.js';
import { logger } from '../src/logger.js';
import { runSimulatedCall } from '../src/demo/simulate.js';

/**
 * Run a simulated inbound call from the command line and print the transcript
 * and metrics. Works without credentials (mock backend).
 */
async function main(): Promise<void> {
  const config = loadConfig();
  logger.info(
    { realtimeBackend: config.realtimeBackend, telnyxBackend: config.telnyxBackend },
    'Running simulated call',
  );

  const result = await runSimulatedCall(config, logger);

  console.log('\n=== Call transcript ===');
  for (const entry of result.transcript) {
    const who = entry.role === 'agent' ? 'AI Agent' : 'Caller  ';
    console.log(`${who} | ${entry.text}`);
  }

  console.log('\n=== Metrics ===');
  console.log(`Caller frames in : ${result.metrics.framesFromCaller}`);
  console.log(`Agent frames out : ${result.metrics.framesToCaller}`);
  console.log(`Agent audio bytes: ${result.metrics.bytesToCaller}`);
  console.log(`Duration         : ${result.durationMs} ms`);
  console.log(`WAV payload bytes : ${Buffer.from(result.agentAudioWavBase64, 'base64').length}`);
}

main().catch((err) => {
  logger.error({ err }, 'Demo failed');
  process.exit(1);
});
