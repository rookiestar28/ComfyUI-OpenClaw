import { vi } from "vitest";

if (!Element.prototype.scrollIntoView) {
    Element.prototype.scrollIntoView = vi.fn();
}

if (!globalThis.alert) {
    globalThis.alert = vi.fn();
}

if (!globalThis.confirm) {
    globalThis.confirm = vi.fn(() => true);
}
