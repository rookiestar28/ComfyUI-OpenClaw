function resolveMutationObserver(mount, options) {
    if (Object.prototype.hasOwnProperty.call(options, "MutationObserverClass")) {
        return options.MutationObserverClass;
    }
    return mount?.ownerDocument?.defaultView?.MutationObserver ??
        globalThis.MutationObserver;
}

export function watchOpenClawSidebarOwnership(
    mount,
    root,
    onOwnershipLost,
    options = {}
) {
    const MutationObserverClass = resolveMutationObserver(mount, options);
    if (
        !mount ||
        !root ||
        typeof onOwnershipLost !== "function" ||
        typeof MutationObserverClass !== "function"
    ) {
        return () => {};
    }

    let active = true;
    let mountRef = mount;
    let rootRef = root;
    let callbackRef = onOwnershipLost;
    let observer = null;

    const disconnect = () => {
        if (!active) return;
        active = false;
        observer?.disconnect();
        observer = null;
        mountRef = null;
        rootRef = null;
        callbackRef = null;
    };

    const retainsExclusiveRoot = () =>
        rootRef?.parentNode === mountRef &&
        mountRef?.childNodes?.length === 1 &&
        mountRef.firstChild === rootRef;

    const handleMutation = () => {
        if (!active || retainsExclusiveRoot()) return;
        const notifyOwnershipLost = callbackRef;
        // CRITICAL: disconnect before releasing layout/metadata. A stale observer callback
        // must never restore OpenClaw state over the next extension's shared-mount render.
        disconnect();
        // IMPORTANT: ownership cleanup is notification-only. Never clear, reparent, or
        // inspect foreign DOM here; doing so would corrupt the incoming sidebar extension.
        notifyOwnershipLost?.();
    };

    observer = new MutationObserverClass(handleMutation);
    observer.observe(mount, { childList: true });

    // IMPORTANT: the exact root must remain the mount's only direct child. Checking only
    // contains(root) misses incoming extensions that append beside OpenClaw without destroy.
    handleMutation();
    return disconnect;
}
