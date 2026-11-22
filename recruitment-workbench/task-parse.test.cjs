const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");
const path = require("node:path");

test("task parsing debounces, waits for IME, and ignores an older response", async () => {
  const handlers = {};
  const result = {innerHTML: ""};
  const requests = [];
  const timers = new Map();
  let nextTimer = 0;
  const context = vm.createContext({
    crypto: {randomUUID: () => "00000000-0000-0000-0000-000000000001"},
    document: {
      querySelector: selector => selector === "#plan-result" ? result : {
        addEventListener: (event, handler) => { handlers[selector + event] = handler; },
      },
      addEventListener() {},
    },
    window: {addEventListener() {}},
    setTimeout: (callback, delay) => {
      const id = ++nextTimer;
      timers.set(id, {callback, delay});
      return id;
    },
    clearTimeout: id => timers.delete(id),
    fetch: (url, options) => {
      if (url !== "/api/plan") return new Promise(() => {});
      return new Promise(resolve => requests.push({body: JSON.parse(options.body), resolve}));
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "app.js"), "utf8"), context);
  const emit = (event, id, value, isComposing = false) =>
    handlers["#content" + event]({target: {id, value}, isComposing});
  const fire = () => {
    assert.equal(timers.size, 1);
    const [id, timer] = timers.entries().next().value;
    assert.equal(timer.delay, 650);
    timers.delete(id);
    return timer.callback();
  };
  const response = (level, blockers) => ({ok: true, json: async () => ({
    task: {hard_filters: [{field: "education_level", operator: "equals", value: level}], soft_preferences: []},
    plan: {blockers},
    parsing_metadata: {mode: "rule_fallback", fallback_reason: "timeout"},
  })});

  emit("change", "job", "测试岗位");
  emit("input", "request", "本", true);
  assert.equal(timers.size, 0);
  emit("compositionend", "request", "本科");
  const first = fire();
  assert.equal(requests[0].body.request, "本科");
  emit("input", "request", "硕士");
  emit("input", "request", "硕士及以上");
  const second = fire();
  assert.equal(requests[1].body.request, "硕士及以上");
  requests[1].resolve(response("master", ["preview_requires_matching_rules"]));
  await second;
  requests[0].resolve(response("bachelor", []));
  await first;
  assert.match(result.innerHTML, /硕士/);
  assert.match(result.innerHTML, /模型请求超时/);
  assert.doesNotMatch(result.innerHTML, /本科|data-run-plan/);

  vm.runInContext(`
    state.plan = {
      task: {hard_filters: [], soft_preferences: []},
      plan: {blockers: []},
      snapshot: {status: "draft", expires_at: Date.now()/1000+60}
    };
    updatePlanResult();
  `, context);
  assert.match(result.innerHTML, /data-confirm-plan/);
  assert.doesNotMatch(result.innerHTML, /data-run-plan/);
  vm.runInContext('state.plan.snapshot.status="confirmed"; updatePlanResult()', context);
  assert.match(result.innerHTML, /data-run-plan/);
  vm.runInContext('state.editPlan=true; updatePlanResult()', context);
  assert.doesNotMatch(result.innerHTML, /data-run-plan/);
  vm.runInContext('state.editPlan=false; state.plan.snapshot.expires_at=1; updatePlanResult()', context);
  assert.match(result.innerHTML, /已过期/);
  assert.doesNotMatch(result.innerHTML, /data-run-plan/);
  emit("change", "job", "另一岗位");
  assert.equal(result.innerHTML, "");
});
