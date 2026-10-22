const test = require("node:test");
const assert = require("node:assert");

function applyPatch(doc, ops) {
    let next = JSON.parse(JSON.stringify(doc));
    for (const op of ops) {
        const parts = op.path.split("/").slice(1).map(p => p.replace(/~1/g, "/").replace(/~0/g, "~"));
        if (parts.length === 0) {
            if (op.op === "replace") next = op.value;
            continue;
        }
        const last = parts.pop();
        let curr = next;
        for (const p of parts) {
            if (curr[p] === undefined) curr[p] = {};
            curr = curr[p];
        }
        if (op.op === "add") {
            if (Array.isArray(curr)) {
                if (last === "-") curr.push(op.value);
                else curr.splice(parseInt(last, 10), 0, op.value);
            } else {
                curr[last] = op.value;
            }
        } else if (op.op === "replace") {
            curr[last] = op.value;
        } else if (op.op === "remove") {
            if (Array.isArray(curr)) curr.splice(parseInt(last, 10), 1);
            else delete curr[last];
        }
    }
    return next;
}

test("patch reducer updates progress and appends item", () => {
    const doc = { title: "Trip", items: [], budget: 1000, progress: 0 };
    const res = applyPatch(doc, [
        { op: "replace", path: "/progress", value: 50 },
        { op: "add", path: "/items/-", value: { id: "item-1", cost: 100 } }
    ]);
    assert.strictEqual(res.progress, 50);
    assert.strictEqual(res.items.length, 1);
    assert.strictEqual(res.items[0].cost, 100);
});

test("patch reducer removes item", () => {
    const doc = { title: "Trip", items: [{ id: "item-1" }], budget: 1000, progress: 50 };
    const res = applyPatch(doc, [{ op: "remove", path: "/items/0" }]);
    assert.strictEqual(res.items.length, 0);
});
