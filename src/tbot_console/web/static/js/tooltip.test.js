import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { tipPosition } from "./tooltip.js";

const viewport = { width: 1000, height: 800 };
const tip = { width: 200, height: 60 };

describe("tipPosition", () => {
  it("ставит под якорем по центру", () => {
    const spot = tipPosition({ left: 400, top: 100, width: 100, height: 40 }, tip, viewport);
    assert.equal(spot.placement, "below");
    assert.equal(spot.top, 148);
    assert.equal(spot.left, 350);
  });

  it("переносит наверх, когда снизу не помещается", () => {
    const spot = tipPosition({ left: 400, top: 700, width: 100, height: 40 }, tip, viewport);
    assert.equal(spot.placement, "above");
    assert.equal(spot.top, 632);
  });

  it("остаётся снизу, если сверху тоже нет места", () => {
    const tall = { width: 200, height: 780 };
    const spot = tipPosition({ left: 400, top: 10, width: 100, height: 40 }, tall, viewport);
    assert.equal(spot.placement, "below");
  });

  it("прижимает к краям экрана", () => {
    assert.equal(tipPosition({ left: 0, top: 10, width: 20, height: 20 }, tip, viewport).left, 8);
    assert.equal(
      tipPosition({ left: 990, top: 10, width: 20, height: 20 }, tip, viewport).left,
      792,
    );
  });

  it("не уезжает влево на узком экране", () => {
    const narrow = { width: 120, height: 800 };
    const spot = tipPosition({ left: 10, top: 10, width: 20, height: 20 }, tip, narrow);
    assert.equal(spot.left, 8);
  });
});
