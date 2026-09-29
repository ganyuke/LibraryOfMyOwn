// Admin page enhancements (external file so the CSP can forbid inline scripts).
document.querySelectorAll(".git-remote-copy").forEach(function (button) {
  button.addEventListener("click", function () {
    const input = document.getElementById(button.dataset.copyTarget);
    if (!input) {
      return;
    }
    const value = input.value;
    const done = function () {
      const label = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(function () {
        button.textContent = label;
      }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done);
      return;
    }
    input.select();
    input.setSelectionRange(0, value.length);
    document.execCommand("copy");
    done();
  });
});

(function () {
  const defaultRule = document.getElementById("default-author-rule");
  const defaultField = document.getElementById("default-author-field");
  const exceptionMode = document.getElementById("exception-mode");
  const customField = document.getElementById("exception-custom-field");

  function syncDefaultAuthorField() {
    if (!defaultRule || !defaultField) {
      return;
    }
    defaultField.hidden = defaultRule.value === "earliest";
  }

  function syncExceptionCustomField() {
    if (!exceptionMode || !customField) {
      return;
    }
    const showCustom = exceptionMode.value === "custom";
    customField.hidden = !showCustom;
    const input = customField.querySelector("input");
    if (input) {
      input.required = showCustom;
    }
  }

  if (defaultRule) {
    defaultRule.addEventListener("change", syncDefaultAuthorField);
    syncDefaultAuthorField();
  }

  if (exceptionMode) {
    exceptionMode.addEventListener("change", syncExceptionCustomField);
    syncExceptionCustomField();
  }
})();

document.querySelectorAll("select[data-autosubmit]").forEach(function (select) {
  select.addEventListener("change", function () {
    if (select.form) {
      select.form.submit();
    }
  });
});
