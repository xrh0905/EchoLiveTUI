/* Run real, unmodified upstream methods with in-memory browser/clock stubs.
 * This is a protocol investigation, not a browser or OBS integration test.
 * Usage: node scripts/probe-upstream.cjs [.reference/Echo-Live]
 */
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const vm = require('node:vm');
const repo = process.argv[2] || '.reference/Echo-Live';
let count = 0;
function check(name, fn) {
    fn();
    console.log(`PASS ${name}`);
    count++;
}

function load(ref) {
    const timers = new Map();
    let timerID = 0;
    const ctx = vm.createContext({
        EchoLiveTools: { defineObjectPropertyReadOnly: Object.assign, getUUID: () => 'stub-live' },
        EchoLiveEventManager: class { on() {} once() {} off() {} emit() {} },
        echoLiveSystem: { setupModule() {}, hook: { trigger() {} } },
        BroadcastChannel: class {
            constructor() { this.sent = []; }
            postMessage(data) { this.sent.push(data); }
        },
        performance: { now: () => 0 },
        setTimeout: (fn, ms) => { timers.set(++timerID, { fn, ms }); return timerID; },
        clearTimeout: id => timers.delete(id),
    });
    for (const path of ['config.js', 'res/class/EchoLiveBroadcast.js', 'res/class/EchoLive.js']) {
        const source = execFileSync('git', ['-C', repo, 'show', `${ref}:${path}`], { encoding: 'utf8' });
        vm.runInContext(source, ctx, { filename: `${ref}/${path}` });
    }
    vm.runInContext(`globalThis.api = { config, EchoLiveBroadcast,
        EchoLiveBroadcastClient, EchoLiveBroadcastPortal, EchoLiveBroadcastHistory, EchoLive };`, ctx);
    return { ...ctx.api, timers };
}

for (const ref of ['1.6.6', '1.8.12']) {
    const api = load(ref);
    const { config, EchoLiveBroadcast: Base, EchoLiveBroadcastClient: Client,
            EchoLiveBroadcastPortal: Portal, EchoLiveBroadcastHistory: History } = api;
    const endpoint = new Base('test', config);
    endpoint.uuid = 'live-uuid';
    endpoint.custom.name = 'main';
    endpoint.type = 'live';
    endpoint.targetTypeCheck = Client.prototype.targetTypeCheck;
    check(`${ref}: object target rejected; UUID/name/role accepted`, () => {
        assert.equal(endpoint.checkTargetIsSelf({ name: 'main' }), false);
        for (const target of ['live-uuid', '@main', '@__live', '@__client']) {
            assert.equal(endpoint.checkTargetIsSelf(target), true);
        }
        assert.equal(endpoint.checkTargetIsSelf('@__history'), false);
        assert.equal(endpoint.checkTargetIsSelf(undefined, true), false);
        assert.equal(endpoint.checkTargetIsSelf('live-uuid', true), true);
    });
    check(`${ref}: target array is ordered, not an unordered include/exclude set`, () => {
        assert.equal(endpoint.checkTargetIsSelf(['@__live', '-@main']), true);
        assert.equal(endpoint.checkTargetIsSelf(['-@main', '@__live']), false);
    });
    const ws = [];
    endpoint.websocket = { send: raw => ws.push(JSON.parse(raw)) };
    check(`${ref}: a send uses BOTH BroadcastChannel and WS by default`, () => {
        endpoint.sendData({ message: 'test' }, 'echo_printing');
        assert.equal(endpoint.broadcast.sent.length, 1);
        assert.equal(ws.length, 1);
    });
    check(`${ref}: disable_broadcast suppresses send but keeps WS`, () => {
        config.editor.websocket.disable_broadcast = true;
        endpoint.sendData({ message: 'test 2' }, 'echo_printing');
        assert.equal(endpoint.broadcast.sent.length, 1);
        assert.equal(ws.length, 2);
    });
    check(`${ref}: WS receive does NOT automatically rebroadcast`, () => {
        const received = [];
        endpoint.event.validMessage = data => received.push(data);
        Client.prototype.__readWebsocketMessage.call(endpoint, JSON.stringify({
            action: 'message_data', from: { uuid: 'sender' }, data: {},
        }));
        assert.equal(received.length, 1);
        assert.equal(endpoint.broadcast.sent.length, 1);
        assert.equal(ws.length, 2);
    });
    check(`${ref}: disable_broadcast does NOT disable channel receive`, () => {
        let received = 0;
        endpoint.event.validMessage = () => received++;
        endpoint.broadcast.onmessage({ data: { action: 'message_data', from: { uuid: 'sender' }, data: {} } });
        assert.equal(received, 1);
    });
    check(`${ref}: history consumes echo_printing, not message_data or echo_next`, () => {
        const messages = [];
        const listener = { echoLiveHistory: { send: data => messages.push(data) } };
        for (const action of ['message_data', 'echo_next', 'echo_printing']) {
            History.prototype.getDataHistory({ action, data: { username: 'User', message: 'Hi' } }, listener);
        }
        assert.equal(messages.length, 1);
        assert.equal(messages[0].message, 'Hi');
    });
    check(`${ref}: editor_typing support matches the pinned version`, () => {
        assert.equal(Base.API_NAME_EDITOR_TYPING === 'editor_typing', ref === '1.8.12');
    });
    if (ref === '1.8.12') {
        // Skip DOM/theme initialization; instantiate normally to retain private fields.
        api.EchoLive.prototype.init = function () {};
        const live = new api.EchoLive({ printSpeedChange: 30 }, config);
        config.echolive.typing.enable = true;
        check(`${ref}: typing heartbeat expires after 3000ms`, () => {
            live.setTypingEditor('editor-uuid', { username: 'User' });
            assert.equal(live.inTypingEditor.size, 1);
            const timer = api.timers.get(live.getTypingEditor('editor-uuid').timer);
            assert.equal(timer.ms, 3000);
            timer.fn();
            assert.equal(live.inTypingEditor.size, 0);
        });
        check(`${ref}: message_data clears typing for the SAME sender UUID`, () => {
            live.setTypingEditor('editor-uuid', { username: 'User' });
            live.send = () => {};
            Portal.prototype.getDataPortal({ action: 'message_data', from: { uuid: 'other' }, data: {} }, { echolive: live });
            assert.equal(live.inTypingEditor.size, 1);
            Portal.prototype.getDataPortal({ action: 'message_data', from: { uuid: 'editor-uuid' }, data: {} }, { echolive: live });
            assert.equal(live.inTypingEditor.size, 0);
        });
    }
}
console.log(`${count} upstream behavior checks passed.`);
