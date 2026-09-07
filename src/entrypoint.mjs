#!/usr/bin/env node
// Dispatch before importing the collector graph. A static import here would
// make short-lived AgentCore context registration pay the daemon startup cost.
if (process.argv[2] === 'invocation-context') {
  const { runInvocationContextCommand } = await import('./invocation-context.js');
  process.exitCode = await runInvocationContextCommand(process.argv.slice(3));
} else {
  // Preserve the existing CLI, process argv/PID and collector's native guard.
  await import('./collector.js');
}
