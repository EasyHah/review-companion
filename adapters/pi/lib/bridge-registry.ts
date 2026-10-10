import { BridgeClient, projectRoot } from './bridge-client.ts';

// TUI owns one foreground session. Web/RPC hosts can run many in one process.
export function createBridgeRegistry(factory = () => new BridgeClient()) {
  const slots = new Map();
  function identity(ctx) {
    const cwd = projectRoot(ctx.cwd);
    return { cwd, key: ctx.mode === 'tui' ? 'tui' : JSON.stringify([cwd, ctx.sessionManager.getSessionId()]), host: ctx.mode === 'tui' ? 'pi' : 'pi_web' };
  }
  function close(key, slot) {
    slot.closed = true;
    slot.client?.stop();
    if (slots.get(key) === slot) slots.delete(key);
  }
  return {
    async ensure(ctx) {
      const { cwd, key, host } = identity(ctx);
      let slot = slots.get(key);
      if (slot && slot.cwd !== cwd) { close(key, slot); slot = undefined; }
      if (!slot) { slot = { cwd, closed: false }; slots.set(key, slot); }
      if (slot.client?.connection) return slot.client;
      if (!slot.connecting) {
        const client = factory();
        slot.client = client;
        slot.connecting = (async () => {
          try {
            await client.start(cwd, process.env, { host });
            if (slot.closed) throw new Error('回顾会话已关闭');
            return client;
          } catch (error) { close(key, slot); throw error; }
          finally { slot.connecting = undefined; }
        })();
      }
      return slot.connecting;
    },
    release(ctx, reason) {
      if (reason === 'reload' || (ctx.mode === 'tui' && reason !== 'quit')) return;
      const { key } = identity(ctx), slot = slots.get(key);
      if (slot) close(key, slot);
    },
    peek(ctx) { return slots.get(identity(ctx).key)?.client; },
  };
}

export function bridgeRegistry() {
  const key = Symbol.for('review-companion-pi.bridges.v2');
  return globalThis[key] ?? (globalThis[key] = createBridgeRegistry());
}
