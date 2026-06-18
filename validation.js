const form = document.getElementById("applicationForm");
const successMessage = document.getElementById("successMessage");
const volumeWarning = document.getElementById("volumeWarning");
const commentsInput = document.getElementById("comments");
const commentsCounter = document.getElementById("commentsCounter");

const fields = {
  companyName: document.getElementById("companyName"),
  contactPerson: document.getElementById("contactPerson"),
  corporateEmail: document.getElementById("corporateEmail"),
  phone: document.getElementById("phone"),
  website: document.getElementById("website"),
  country: document.getElementById("country"),
  productType: document.getElementById("productType"),
  monthlyVolume: document.getElementById("monthlyVolume"),
  privacyPolicy: document.getElementById("privacyPolicy"),
  comments: document.getElementById("comments")
};

const errorIds = {
  companyName: "companyNameError",
  contactPerson: "contactPersonError",
  corporateEmail: "corporateEmailError",
  phone: "phoneError",
  website: "websiteError",
  country: "countryError",
  productType: "productTypeError",
  monthlyVolume: "monthlyVolumeError",
  services: "servicesError",
  current3pl: "current3plError",
  comments: "commentsError",
  privacyPolicy: "privacyPolicyError"
};

const messages = {
  companyName: "El nombre de la empresa debe tener al menos 2 caracteres",
  contactPerson: "Ingresa nombre y apellido del contacto",
  corporateEmail: "Ingresa un email corporativo válido (ejemplo: nombre@empresa.com)",
  phone: "El teléfono debe incluir código de país (ejemplo: +1 213 555 0147)",
  website: "Si incluyes sitio web, debe ser una URL válida",
  country: "Selecciona el país de operación principal",
  productType: "Selecciona el tipo de producto que manejas",
  monthlyVolume: "Selecciona el volumen mensual estimado",
  services: "Selecciona al menos un servicio de interés",
  current3pl: "Indica si actualmente trabajas con otro proveedor logístico",
  privacyPolicy: "Debes aceptar la política de privacidad para continuar"
};

const validators = {
  companyName: (value) => value.trim().length >= 2,
  contactPerson: (value) => /^\S+\s+\S+.*$/.test(value.trim()),
  corporateEmail: (value) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim()),
  phone: (value) => /^\+\d{1,3}[\s-]?\d[\d\s-]{6,}$/.test(value.trim()),
  website: (value) => value.trim() === "" || /^https?:\/\/.+\..+/.test(value.trim()),
  country: (value) => value !== "",
  productType: (value) => value !== "",
  monthlyVolume: (value) => value !== "",
  comments: (value) => value.length <= 500,
  privacyPolicy: (checked) => checked
};

function getErrorElement(fieldName) {
  return document.getElementById(errorIds[fieldName]);
}

function showError(fieldName, message) {
  const errorElement = getErrorElement(fieldName);
  if (!errorElement) return;
  errorElement.textContent = message;
  errorElement.classList.remove("hidden");
}

function hideError(fieldName) {
  const errorElement = getErrorElement(fieldName);
  if (!errorElement) return;
  errorElement.textContent = "";
  errorElement.classList.add("hidden");
}

function getCheckedServices() {
  return Array.from(document.querySelectorAll('input[name="services"]:checked'));
}

function getCurrent3plValue() {
  const selected = document.querySelector('input[name="current3pl"]:checked');
  return selected ? selected.value : "";
}

function validateComments() {
  const value = fields.comments.value;
  const remaining = 500 - value.length;
  commentsCounter.textContent = `${remaining} caracteres disponibles`;

  if (!validators.comments(value)) {
    showError("comments", `Los comentarios no pueden exceder 500 caracteres (quedan ${remaining})`);
    return false;
  }

  hideError("comments");
  return true;
}

function validateLowVolumeWarning() {
  const volume = fields.monthlyVolume.value;
  const productType = fields.productType.value;
  const show = volume === "0-100" && productType !== "";
  volumeWarning.classList.toggle("hidden", !show);
}

function validateField(fieldName) {
  if (fieldName === "services") {
    const valid = getCheckedServices().length > 0;
    if (!valid) {
      showError("services", messages.services);
      return false;
    }
    hideError("services");
    return true;
  }

  if (fieldName === "current3pl") {
    const valid = getCurrent3plValue() !== "";
    if (!valid) {
      showError("current3pl", messages.current3pl);
      return false;
    }
    hideError("current3pl");
    return true;
  }

  if (fieldName === "comments") {
    return validateComments();
  }

  const field = fields[fieldName];
  if (!field) return true;

  const isValid = fieldName === "privacyPolicy"
    ? validators[fieldName](field.checked)
    : validators[fieldName](field.value);

  if (!isValid) {
    showError(fieldName, messages[fieldName]);
    return false;
  }

  hideError(fieldName);
  return true;
}

function validateAllFields() {
  const basicFieldOrder = [
    "companyName",
    "contactPerson",
    "corporateEmail",
    "phone",
    "website",
    "country",
    "productType",
    "monthlyVolume",
    "comments",
    "privacyPolicy"
  ];

  const results = basicFieldOrder.map((fieldName) => validateField(fieldName));
  const servicesValid = validateField("services");
  const current3plValid = validateField("current3pl");

  return [...results, servicesValid, current3plValid].every(Boolean);
}

function attachFieldListeners() {
  [
    "companyName",
    "contactPerson",
    "corporateEmail",
    "phone",
    "website"
  ].forEach((fieldName) => {
    const field = fields[fieldName];
    field.addEventListener("input", () => validateField(fieldName));
    field.addEventListener("blur", () => validateField(fieldName));
  });

  ["country", "productType", "monthlyVolume"].forEach((fieldName) => {
    const field = fields[fieldName];
    field.addEventListener("change", () => {
      validateField(fieldName);
      validateLowVolumeWarning();
    });
    field.addEventListener("blur", () => validateField(fieldName));
  });

  commentsInput.addEventListener("input", () => validateField("comments"));
  commentsInput.addEventListener("blur", () => validateField("comments"));

  fields.privacyPolicy.addEventListener("change", () => validateField("privacyPolicy"));
  fields.privacyPolicy.addEventListener("blur", () => validateField("privacyPolicy"));

  document.querySelectorAll('input[name="services"]').forEach((checkbox) => {
    checkbox.addEventListener("change", () => validateField("services"));
    checkbox.addEventListener("blur", () => validateField("services"));
  });

  document.querySelectorAll('input[name="current3pl"]').forEach((radio) => {
    radio.addEventListener("change", () => validateField("current3pl"));
    radio.addEventListener("blur", () => validateField("current3pl"));
  });
}

function clearFeedback() {
  Object.keys(errorIds).forEach((fieldName) => hideError(fieldName));
  successMessage.classList.add("hidden");
  successMessage.innerHTML = "";
  volumeWarning.classList.add("hidden");
  commentsCounter.textContent = "500 caracteres disponibles";
}

function clearErrorsAndWarning() {
  Object.keys(errorIds).forEach((fieldName) => hideError(fieldName));
  volumeWarning.classList.add("hidden");
  commentsCounter.textContent = "500 caracteres disponibles";
}

form.addEventListener("submit", (event) => {
  event.preventDefault();

  successMessage.classList.add("hidden");
  successMessage.innerHTML = "";

  const isValid = validateAllFields();
  validateLowVolumeWarning();

  if (!isValid) {
    const firstError = document.querySelector('[role="alert"]:not(.hidden)');
    if (firstError) {
      const fieldWithError = firstError.id.replace("Error", "");
      const focusTarget = document.getElementById(fieldWithError);
      if (focusTarget) focusTarget.focus();
    }
    return;
  }

  form.reset();
  clearErrorsAndWarning();

  successMessage.innerHTML = `
    <p class="font-semibold">¡Gracias por tu interés en TrackFlow!</p>
    <p class="mt-2">Hemos recibido tu solicitud. Nuestro equipo comercial revisará tu información y te contactará en las próximas 24-48 horas para agendar una llamada y conocer tus necesidades logísticas en detalle.</p>
    <p class="mt-2">Si tienes alguna consulta urgente, escríbenos directamente a comercial@trackflow.com</p>
  `;
  successMessage.classList.remove("hidden");
});

form.addEventListener("reset", () => {
  window.requestAnimationFrame(() => {
    clearFeedback();
  });
});

attachFieldListeners();
validateComments();
validateLowVolumeWarning();
