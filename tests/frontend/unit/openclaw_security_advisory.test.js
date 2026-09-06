import { describe, expect, it } from "vitest";

import { buildDoctorAdvisoryBanner } from "../../../web/openclaw_security_advisory.js";

describe("openclaw security advisory banner helper", () => {
    it("returns null when advisory status is not affected", () => {
        const banner = buildDoctorAdvisoryBanner({
            advisory_status: {
                affected: false,
                high_severity_affected: 0,
            },
        });
        expect(banner).toBeNull();
    });

    it("returns null when affected but no high severity advisory", () => {
        const banner = buildDoctorAdvisoryBanner({
            advisory_status: {
                affected: true,
                high_severity_affected: 0,
                mitigation: "Upgrade eventually.",
            },
        });
        expect(banner).toBeNull();
    });

    it("builds deterministic error banner for high severity affected advisory", () => {
        const banner = buildDoctorAdvisoryBanner({
            advisory_status: {
                affected: true,
                high_severity_affected: 2,
                mitigation: "Upgrade to >=0.3.4 immediately.",
            },
        });
        expect(banner).not.toBeNull();
        expect(banner.id).toBe("security_advisory_high_severity");
        expect(banner.severity).toBe("error");
        expect(banner.message).toContain("2 high-severity issue(s)");
        expect(banner.message).toContain("Upgrade to >=0.3.4 immediately.");
        expect(banner.action).toEqual({
            type: "tab",
            payload: "settings",
            label: "Review",
        });
    });
});
