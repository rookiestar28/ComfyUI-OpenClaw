export function createMockComfyUIApp() {
  const settingsStore = new Map();

  const registerSidebarTab = (tabDefinition) => {
    const root = document.getElementById('mock-sidebar-tabs');
    const panel = document.createElement('div');
    panel.className = 'side-bar-panel p-splitterpanel';
    panel.dataset.mockInitialWidth = '340';

    const content = document.createElement('div');
    content.className = 'sidebar-content-container';

    const host = document.createElement('div');
    host.id = `sidebar-tab-${tabDefinition.id}`;
    host.style.height = '100vh';

    content.appendChild(host);
    panel.appendChild(content);
    root.replaceChildren(panel);

    window.__openclawMockSidebarTab = {
      definition: tabDefinition,
      destroy: () => tabDefinition.destroy?.(),
      render: () => tabDefinition.render(host),
    };
    tabDefinition.render(host);
  };

  const app = {
    ui: {
      settings: {
        addSetting: ({ id, defaultValue }) => {
          if (!settingsStore.has(id)) settingsStore.set(id, defaultValue);
        },
        getSettingValue: (id, fallback) => {
          return settingsStore.has(id) ? settingsStore.get(id) : fallback;
        },
      },
    },

    extensionManager: {
      sidebarTab: { registerSidebarTab },
      registerSidebarTab,
    },

    registerExtension: ({ name, setup }) => {
      // Simulate ComfyUI calling setup immediately.
      return Promise.resolve(setup());
    },
  };

  return app;
}
