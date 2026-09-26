import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { esc, fmtCount, fmtDuration, fmtNum, fmtPrice, fmtUsd, pnlClass } from "./util.js";

describe("esc", () => {
  it("экранирует все пять опасных символов", () => {
    assert.equal(esc(`<a href="x">&'`), "&lt;a href=&quot;x&quot;&gt;&amp;&#39;");
  });

  it("нейтрализует инъекцию тега/атрибута (XSS-поверхность innerHTML)", () => {
    const out = esc(`<img src=x onerror="alert(1)">`);
    assert.ok(!out.includes("<"));
    assert.ok(!out.includes(">"));
    assert.ok(!out.includes('"'));
  });

  it("null/undefined → пустая строка, число → строка", () => {
    assert.equal(esc(null), "");
    assert.equal(esc(undefined), "");
    assert.equal(esc(42), "42");
  });
});

describe("fmtDuration", () => {
  it("часы и минуты при >= 1ч", () => {
    assert.equal(fmtDuration(3661), "1ч 1м");
    assert.equal(fmtDuration(7200), "2ч 0м");
  });

  it("минуты и секунды при < 1ч", () => {
    assert.equal(fmtDuration(61), "1м 1с");
    assert.equal(fmtDuration(3599), "59м 59с");
  });

  it("только секунды при < 1м", () => {
    assert.equal(fmtDuration(5), "5с");
    assert.equal(fmtDuration(59), "59с");
  });

  it("ноль, отрицательное и не-число → тире", () => {
    assert.equal(fmtDuration(0), "—");
    assert.equal(fmtDuration(-10), "—");
    assert.equal(fmtDuration(Number.NaN), "—");
    assert.equal(fmtDuration(Infinity), "—");
  });
});

describe("fmtCount", () => {
  it("миллионы и тысячи сокращаются, меньшее округляется", () => {
    assert.equal(fmtCount(1_500_000), "1.5 млн");
    assert.equal(fmtCount(12_345), "12 тыс");
    assert.equal(fmtCount(42.4), "42");
  });

  it("не-число и бесконечность → тире", () => {
    for (const value of [null, undefined, Number.NaN, Number.POSITIVE_INFINITY, "12", {}]) {
      assert.equal(fmtCount(value), "—");
    }
  });
});

describe("fmtPrice", () => {
  it("тысячи через апостроф, без пробелов — ряд цен читается по числу на чип", () => {
    assert.ok(!/[\s\u00a0\u202f]/.test(fmtPrice(78424.5)));
    assert.equal(fmtPrice(78424.5).replace(",", "."), "78'424.5");
    assert.equal(fmtPrice(65000, 0), "65'000");
    assert.equal(fmtPrice(1234567.89, 2).replace(",", "."), "1'234'567.89");
    assert.equal(fmtPrice(950), "950");
  });

  it("null/undefined/NaN → тире, дробность ограничивается", () => {
    assert.equal(fmtPrice(null), "—");
    assert.equal(fmtPrice(undefined), "—");
    assert.equal(fmtPrice(Number.NaN), "—");
    assert.equal(fmtPrice(1.23456789, 2).replace(",", "."), "1.23");
  });
});

describe("fmtNum", () => {
  it("null/undefined/NaN → тире", () => {
    assert.equal(fmtNum(null), "—");
    assert.equal(fmtNum(undefined), "—");
    assert.equal(fmtNum(Number.NaN), "—");
  });

  it("очень малые ненулевые → экспонента", () => {
    assert.equal(fmtNum(0.00001), "1.00e-5");
  });

  it("ноль форматируется как число, не экспонента", () => {
    assert.equal(fmtNum(0), "0");
  });
});

describe("fmtUsd", () => {
  it("минус-знак — типографский, знак доллара", () => {
    assert.equal(fmtUsd(-12.5), "−$12.50");
    assert.equal(fmtUsd(12.5), "$12.50");
  });

  it("null/NaN → тире", () => {
    assert.equal(fmtUsd(null), "—");
    assert.equal(fmtUsd(Number.NaN), "—");
  });

  it(">= 10M — компактная запись", () => {
    assert.match(fmtUsd(15_000_000), /^\$15M$/);
  });
});

describe("pnlClass", () => {
  it("знак определяет класс, ноль/пусто — без класса", () => {
    assert.equal(pnlClass(5), "pos");
    assert.equal(pnlClass(-5), "neg");
    assert.equal(pnlClass(0), "");
    assert.equal(pnlClass(null), "");
  });
});
