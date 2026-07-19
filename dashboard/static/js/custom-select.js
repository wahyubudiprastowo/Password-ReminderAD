(function() {
  const instances = [];

  function closeAll(except) {
    instances.forEach((instance) => {
      if (instance !== except) instance.wrapper.classList.remove("is-open");
    });
  }

  function buildOption(option, instance) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "custom-select__option";
    button.dataset.value = option.value;
    button.textContent = option.textContent;
    if (option.disabled) {
      button.disabled = true;
    }
    button.addEventListener("click", () => {
      if (option.disabled) return;
      instance.select.value = option.value;
      instance.select.dispatchEvent(new Event("change", { bubbles: true }));
      instance.sync();
      instance.wrapper.classList.remove("is-open");
    });
    return button;
  }

  function enhanceSelect(select) {
    if (!select || select.dataset.customSelectReady === "1") return null;
    select.dataset.customSelectReady = "1";

    const wrapper = document.createElement("div");
    wrapper.className = "custom-select";
    if (select.classList.contains("compact-select")) {
      wrapper.classList.add("custom-select-block");
      wrapper.style.width = "100%";
    } else {
      const width = Math.max(Math.ceil(select.getBoundingClientRect().width || 0), 88);
      wrapper.style.width = `${width}px`;
    }
    if (select.style.minWidth) wrapper.style.minWidth = select.style.minWidth;

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "custom-select__trigger";
    trigger.innerHTML = '<span class="custom-select__value"></span><span class="custom-select__chevron" aria-hidden="true"></span>';

    const menu = document.createElement("div");
    menu.className = "custom-select__menu";

    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(select);
    wrapper.appendChild(trigger);
    wrapper.appendChild(menu);
    select.classList.add("native-select-hidden");

    const instance = {
      select,
      wrapper,
      trigger,
      menu,
      lastLength: 0,
      lastValue: "",
      rebuild() {
        menu.innerHTML = "";
        Array.from(select.options).forEach((option) => {
          menu.appendChild(buildOption(option, instance));
        });
        instance.lastLength = select.options.length;
        instance.sync();
      },
      sync() {
        const selectedOption = select.options[select.selectedIndex];
        trigger.querySelector(".custom-select__value").textContent = selectedOption ? selectedOption.textContent : "-";
        menu.querySelectorAll(".custom-select__option").forEach((node) => {
          node.classList.toggle("is-selected", node.dataset.value === select.value);
        });
        wrapper.classList.toggle("is-disabled", !!select.disabled);
        trigger.disabled = !!select.disabled;
        instance.lastValue = select.value;
      }
    };

    trigger.addEventListener("click", (event) => {
      event.stopPropagation();
      if (select.disabled) return;
      const isOpen = wrapper.classList.contains("is-open");
      closeAll(instance);
      wrapper.classList.toggle("is-open", !isOpen);
    });

    select.addEventListener("change", () => instance.sync());
    instance.rebuild();
    instances.push(instance);
    return instance;
  }

  function refreshAll() {
    instances.forEach((instance) => {
      if (instance.select.options.length !== instance.lastLength) {
        instance.rebuild();
        return;
      }
      if (instance.select.value !== instance.lastValue) {
        instance.sync();
      }
    });
  }

  function init(root) {
    (root || document).querySelectorAll("select.select-sm").forEach(enhanceSelect);
  }

  document.addEventListener("DOMContentLoaded", () => {
    init(document);
    setInterval(refreshAll, 250);
  });

  document.addEventListener("click", () => closeAll());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAll();
  });

  window.refreshCustomSelect = function(target) {
    if (!target) {
      refreshAll();
      return;
    }
    if (target.matches && target.matches("select.select-sm")) {
      const existing = instances.find((instance) => instance.select === target);
      if (existing) {
        existing.rebuild();
      } else {
        enhanceSelect(target);
      }
      return;
    }
    if (target.querySelectorAll) {
      init(target);
      refreshAll();
    }
  };
})();
