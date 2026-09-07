# AgentCore lightweight invocation registration

This development branch keeps the managed Runtime command unchanged:

```sh
node /opt/loongsuite-pilot/dist/index.js invocation-context put \
  --agent qoder --message-uuid <uuid>
```

Pass span attributes as a JSON object on stdin. Use `qoder-cn` for the CN
profile. UUID identity, context root, TTL, atomic create, permissions and Hook
lookup all use the existing invocation-context implementation.

The built `dist/index.js` dispatches before loading dependencies:

- `invocation-context`: load the self-contained `dist/invocation-context.js`.
- All other commands, including the default collector and `deploy`: load
  `dist/collector.js`, the original application bundle with its native dependency
  guard. Process argv and PID are unchanged.

Always deploy the complete `dist/` directory, not index.js alone. No Runtime
timeout increase or new storage implementation is needed. Keep business
fail-open behavior when observability registration fails.

Run `npm run typecheck`, the invocation-context unit tests, and
`npm run test:invocation-context-artifact`. The last command tests the built
public entry in an isolated directory without collector/native dependencies or
node_modules, for both CN and Global, including Hook readback and validation.

Local performance must be measured in the actual Runtime container; native
developer-machine timings are not equivalent to an amd64 image on an ARM host.
After deployment, verify real requests and their UUID/context/Hook/Span mapping;
fast registration alone does not prove complete trace collection.
