/**
 * A second custom sidebar tab, registered only inside the real-host smoke lane.
 *
 * The behavior under test is what OpenClaw does when the host hands its shared
 * sidebar mount to a different custom tab without calling a destroy callback
 * first. Observing that needs a second custom tab to hand the mount to, and this
 * is the smallest one that is still a real host registration: it renders one
 * identifiable element into the mount and does nothing else.
 *
 * It deliberately does not clean up after itself. The point of the check is that
 * OpenClaw releases the mount on its own.
 */

import { app } from "../../scripts/app.js";

export const PEER_TAB_ID = "openclaw-smoke-peer";
export const PEER_CONTENT_ID = "openclaw-smoke-peer-content";

app.registerExtension({
    name: "openclaw.smoke.peer",
    async setup() {
        app.extensionManager?.registerSidebarTab?.({
            id: PEER_TAB_ID,
            icon: "pi pi-bookmark",
            title: "Smoke Peer",
            tooltip: "Real-host smoke lane peer tab",
            type: "custom",
            render: (element) => {
                const marker = document.createElement("section");
                marker.id = PEER_CONTENT_ID;
                marker.dataset.openclawSmokePeer = "mounted";
                marker.textContent = "peer tab content";
                element.replaceChildren(marker);
            },
        });
    },
});
