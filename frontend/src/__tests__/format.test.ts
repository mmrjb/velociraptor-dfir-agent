import { describe, expect, it } from "vitest";
import { displayValue, elapsed, statusLabel } from "../lib/format";

describe("format", () => {
  it("pairs every status with a text label", () => {
    expect(statusLabel("empty_or_skipped")).toEqual(["Empty or skipped", "warn"]);
    expect(statusLabel("cancelled")[0]).toBe("Cancelled on server");
    expect(statusLabel("wait_timeout")[0]).toContain("may still run");
    expect(statusLabel("something_new")).toEqual(["something new", "neutral"]);
  });
  it("keeps false, 0, empty and null distinct", () => {
    expect(displayValue(false)).toBe("false");
    expect(displayValue(0)).toBe("0");
    expect(displayValue("")).toBe("");
    expect(displayValue(null)).toBe("null");
  });
  it("formats elapsed time", () => {
    expect(elapsed("2026-01-01T00:00:00Z", "2026-01-01T00:01:05Z")).toBe("1m 5s");
  });
});
