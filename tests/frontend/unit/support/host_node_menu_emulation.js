// Target-owned emulation of how a ComfyUI frontend builds a node's context menu. It exists so unit
// tests can run an extension's real registration code against both host generations without a host.
//
// The current-host emulation is a port of three places in the pinned frontends (git objects of
// Comfy-Org/ComfyUI_frontend tags v1.59.11 and v1.61.8, identical for getNodeMenuOptions in both):
//   src/lib/litegraph/src/contextMenuCompat.ts    LegacyMenuCompat: install, registerWrapper,
//                                                  extractLegacyItems (the accessor that warns)
//   src/composables/useContextMenuTranslation.ts  the wrapper that appends hook items, then legacy items
//   src/scripts/app.ts                             collectNodeMenuItems over the `getNodeMenuItems` hook
// Nothing here imports or executes host code. If a pinned host changes one of those places, this
// port must be re-read against the new source, not adapted until a test passes.

class LegacyMenuCompat {
    constructor(warn) {
        this.warn = warn;
        this.originalMethods = new Map();
        this.hasWarned = new Set();
        this.currentExtension = null;
        this.isExtracting = false;
        this.wrapperMethods = new Map();
        this.preWrapperMethods = new Map();
        this.wrapperInstalled = new Map();
        this.assignments = 0;
    }

    setCurrentExtension(name) {
        this.currentExtension = name;
    }

    registerWrapper(methodName, wrapperFn, preWrapperFn, prototype) {
        this.wrapperMethods.set(methodName, wrapperFn);
        this.preWrapperMethods.set(methodName, preWrapperFn);
        this.wrapperInstalled.set(methodName, !!prototype && prototype[methodName] === wrapperFn);
    }

    install(prototype, methodName) {
        const originalMethod = prototype[methodName];
        this.originalMethods.set(methodName, originalMethod);
        let currentImpl = originalMethod;
        Object.defineProperty(prototype, methodName, {
            configurable: true,
            get() {
                return currentImpl;
            },
            set: (newImpl) => {
                if (!newImpl) return;
                this.assignments += 1;
                const fnKey = `${methodName}:${newImpl.toString().slice(0, 100)}`;
                if (!this.hasWarned.has(fnKey) && this.currentExtension) {
                    this.hasWarned.add(fnKey);
                    this.warn(
                        `%c[DEPRECATED]%c Monkey-patching ${methodName} is deprecated. (Extension: "${this.currentExtension}")\n` +
                            "Please use the new context menu API instead.\n\n" +
                            "See: https://docs.comfy.org/custom-nodes/js/context-menu-migration",
                        "color: orange; font-weight: bold",
                        "color: inherit",
                    );
                }
                currentImpl = newImpl;
            },
        });
    }

    extractLegacyItems(methodName, context, ...args) {
        if (this.isExtracting) return [];
        const originalMethod = this.originalMethods.get(methodName);
        if (!originalMethod) return [];
        try {
            this.isExtracting = true;
            const originalItems = originalMethod.apply(context, args);
            if (!originalItems) return [];
            const currentMethod = context.constructor.prototype[methodName];
            if (!currentMethod || currentMethod === originalMethod) return [];
            const registeredWrapper = this.wrapperMethods.get(methodName);
            if (registeredWrapper && currentMethod === registeredWrapper) return [];
            const preWrapperMethod = this.preWrapperMethods.get(methodName);
            const wrapperWasInstalled = this.wrapperInstalled.get(methodName);
            const shouldSkipWrapper = preWrapperMethod && wrapperWasInstalled && currentMethod !== preWrapperMethod;
            const methodToCall = shouldSkipWrapper ? preWrapperMethod : currentMethod;
            const patchedItems = methodToCall.apply(context, args);
            if (!patchedItems) return [];
            const itemKey = (item) =>
                [item.content ?? "", item.title ?? "", item.className ?? "", item.property ?? "", item.type ?? ""].join("|");
            const isEntry = (item) => item !== null && typeof item === "object" && "content" in item;
            const originalKeys = new Set(originalItems.filter(isEntry).map(itemKey));
            return patchedItems.filter((item) => isEntry(item) && !originalKeys.has(itemKey(item)));
        } catch {
            return [];
        } finally {
            this.isExtracting = false;
        }
    }
}

const copyItems = (items) => items.map((item) => (item === null ? null : { ...item }));

/**
 * A host generation that has the node-menu hook: the accessor on the prototype, the wrapper that
 * merges hook items and legacy items, and `app.collectNodeMenuItems`.
 */
export function installCurrentHostNodeMenu({ app, canvasClass, baseItems, warn }) {
    const extensions = [];
    const compat = new LegacyMenuCompat(warn);

    canvasClass.prototype.getNodeMenuOptions = function getNodeMenuOptions() {
        return copyItems(baseItems);
    };
    compat.install(canvasClass.prototype, "getNodeMenuOptions");
    const nodeMenuFn = canvasClass.prototype.getNodeMenuOptions;
    const wrapper = function getNodeMenuOptionsWithExtensions(...args) {
        const res = nodeMenuFn.apply(this, args);
        for (const item of app.collectNodeMenuItems(args[0])) res.push(item);
        for (const item of compat.extractLegacyItems("getNodeMenuOptions", this, ...args)) res.push(item);
        return res;
    };
    canvasClass.prototype.getNodeMenuOptions = wrapper;
    compat.registerWrapper("getNodeMenuOptions", wrapper, nodeMenuFn, canvasClass.prototype);

    app.registerExtension = (extension) => {
        extensions.push(extension);
    };
    app.collectNodeMenuItems = (node) =>
        extensions.flatMap((extension) =>
            typeof extension.getNodeMenuItems === "function" ? extension.getNodeMenuItems(node) : [],
        );

    return {
        extensions,
        // The extension service marks an extension as current for the duration of its setup hook.
        async runSetup() {
            for (const extension of extensions) {
                compat.setCurrentExtension(extension.name);
                try {
                    await extension.setup?.();
                } finally {
                    compat.setCurrentExtension(null);
                }
            }
        },
        menuFor(node) {
            return canvasClass.prototype.getNodeMenuOptions.call(new canvasClass(), node);
        },
        prototypeAssignmentsByExtensions() {
            // The wrapper installation above is the host's own assignment; only later ones count.
            return Math.max(0, compat.assignments - 1);
        },
    };
}

/** A host generation without the node-menu hook: a plain prototype method and no consumer. */
export function installLegacyHostNodeMenu({ app, canvasClass, baseItems }) {
    const extensions = [];
    canvasClass.prototype.getNodeMenuOptions = function getNodeMenuOptions() {
        return copyItems(baseItems);
    };
    app.registerExtension = (extension) => {
        extensions.push(extension);
    };
    return {
        extensions,
        async runSetup() {
            for (const extension of extensions) await extension.setup?.();
        },
        menuFor(node) {
            return canvasClass.prototype.getNodeMenuOptions.call(new canvasClass(), node);
        },
    };
}
