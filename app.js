/* Strip Bay -- live strips for whatever the receiver hears. */

(function () {
  "use strict";

  var POLL_MS = 1000;
  var STALE_SECONDS = 30;      // paper is fully aged by here
  var COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                 "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

  var state = {
    contacts: [],
    sort: "range",
    filter: "",
    namedOnly: false,
    receiver: null,
    configured: false,
    promptedSetup: false
  };

  /* The app opens the window with ?k=<key>. Keep it in memory and strip it
     from the address bar so it does not linger in history. */
  var KEY = new URLSearchParams(location.search).get("k") || "";
  if (KEY) history.replaceState(null, "", location.pathname);

  function post(path, body) {
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Strip-Key": KEY },
      body: JSON.stringify(body || {})
    }).then(function (response) { return response.json(); });
  }

  var nodes = {};              // icao -> <li>
  var el = {
    strips: document.getElementById("strips"),
    empty: document.getElementById("empty-state"),
    filter: document.getElementById("filter"),
    namedOnly: document.getElementById("only-named"),
    count1090: document.getElementById("count-1090"),
    count978: document.getElementById("count-978"),
    countNamed: document.getElementById("count-named"),
    receiverNote: document.getElementById("receiver-note"),
    registryNote: document.getElementById("registry-note"),
    feedNote: document.getElementById("feed-note"),
    card: document.getElementById("card"),
    cardTail: document.getElementById("card-tail"),
    cardBody: document.getElementById("card-body"),
    setup: document.getElementById("setup"),
    openSetup: document.getElementById("open-setup"),
    hostInput: document.getElementById("host-input"),
    latInput: document.getElementById("lat-input"),
    lonInput: document.getElementById("lon-input"),
    testHost: document.getElementById("test-host"),
    hostResult: document.getElementById("host-result"),
    registryState: document.getElementById("registry-state"),
    buildRegistry: document.getElementById("build-registry"),
    wantOpensky: document.getElementById("want-opensky"),
    buildProgress: document.getElementById("build-progress"),
    buildBar: document.getElementById("build-bar"),
    buildMessage: document.getElementById("build-message"),
    saveSetup: document.getElementById("save-setup"),
    quit: document.getElementById("quit")
  };

  /* ---------- formatting ---------- */

  function altitudeText(contact) {
    if (contact.on_ground) return "GND";
    if (contact.altitude === null || contact.altitude === undefined) return "—";
    var feet = Math.round(contact.altitude);
    if (feet >= 18000) return "FL" + String(Math.round(feet / 100)).padStart(3, "0");
    return feet.toLocaleString() + "\u2009ft";
  }

  function climbMark(contact) {
    var rate = contact.vertical_rate;
    if (!rate || Math.abs(rate) < 200) return "";
    return rate > 0 ? " \u2191" : " \u2193";
  }

  function bearingText(contact) {
    if (contact.bearing === null || contact.bearing === undefined) return "";
    var point = COMPASS[Math.round(contact.bearing / 22.5) % 16];
    return String(contact.bearing).padStart(3, "0") + "\u00b0 " + point;
  }

  function rangeText(contact) {
    if (contact.distance_nm === null || contact.distance_nm === undefined) return "";
    return contact.distance_nm.toFixed(1) + "\u2009nm";
  }

  function aircraftText(owner) {
    var parts = [];
    if (owner.year_built) parts.push(owner.year_built);
    if (owner.manufacturer) parts.push(titleCase(owner.manufacturer));
    if (owner.model) parts.push(owner.model);
    return parts.join(" ");
  }

  function placeText(owner) {
    var parts = [];
    if (owner.city) parts.push(titleCase(owner.city));
    if (owner.region) parts.push(owner.region.toUpperCase());
    else if (owner.country && owner.country !== "US") parts.push(owner.country);
    return parts.join(", ");
  }

  function titleCase(text) {
    return String(text).toLowerCase().replace(/\b[a-z]/g, function (c) {
      return c.toUpperCase();
    });
  }

  function ownerLabel(owner) {
    if (owner && owner.owner) return titleCase(owner.owner);
    return "";
  }

  /* ---------- strip construction ---------- */

  function cell(label, value, quiet) {
    if (!value) return "";
    return '<span class="cell' + (quiet ? " cell--quiet" : "") + '">'
         + "<u>" + label + "</u><b>" + escapeHtml(value) + "</b></span>";
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function stripMarkup(contact) {
    var owner = contact.owner || {};
    var name = ownerLabel(owner);
    var tags = [];

    if (contact.emergency) tags.push('<span class="tag tag--flag">Emergency</span>');
    if (owner.deregistered) tags.push('<span class="tag tag--flag">Deregistered</span>');
    if (owner.resolution === "derived") tags.push('<span class="tag">Not in registry</span>');
    if (owner.block && !name) tags.push('<span class="tag">' + escapeHtml(owner.block) + "</span>");
    if (contact.anonymous) tags.push('<span class="tag">Anonymous</span>');
    if (owner.owner_type) tags.push('<span class="tag">' + escapeHtml(owner.owner_type) + "</span>");

    var remarks = name
      ? '<span class="owner">' + escapeHtml(name) + "</span>"
      : '<span class="owner owner--absent">Owner not on file</span>';

    var place = placeText(owner);
    var machine = aircraftText(owner);
    if (place) remarks += '<span class="meta">' + escapeHtml(place) + "</span>";
    if (machine) remarks += '<span class="meta">' + escapeHtml(machine) + "</span>";
    remarks += tags.join("");

    return ''
      + '<div class="strip__holder"><span class="strip__band">'
      + escapeHtml(contact.bands.join("/")) + "</span></div>"
      + '<div class="strip__content">'
      +   '<div class="strip__data">'
      +     '<span class="strip__tail" data-resolution="' + escapeHtml(owner.resolution || "") + '">'
      +       escapeHtml(owner.tail || "\u2014") + "</span>"
      +     '<span class="strip__hex">' + escapeHtml(contact.icao.toUpperCase()) + "</span>"
      +     cell("CS", contact.callsign)
      +     cell("ALT", altitudeText(contact) + climbMark(contact))
      +     cell("GS", contact.ground_speed ? Math.round(contact.ground_speed) + "\u2009kt" : "")
      +     cell("RNG", rangeText(contact))
      +     cell("BRG", bearingText(contact))
      +     cell("SQK", contact.squawk, true)
      +   "</div>"
      +   '<div class="strip__remarks">' + remarks + "</div>"
      + "</div>";
  }

  function ensureStrip(contact) {
    var node = nodes[contact.icao];
    if (!node) {
      node = document.createElement("li");
      node.className = "strip strip--arriving";
      node.addEventListener("animationend", function () {
        node.classList.remove("strip--arriving");
      });
      node.tabIndex = 0;
      node.setAttribute("role", "button");
      node.dataset.icao = contact.icao;
      node.addEventListener("click", function () { openCard(contact.icao); });
      node.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openCard(contact.icao);
        }
      });
      nodes[contact.icao] = node;
    }

    var markup = stripMarkup(contact);
    if (node._markup !== markup) {
      node.innerHTML = markup;
      node._markup = markup;
    }

    node.dataset.band = contact.bands.indexOf("1090") >= 0 ? "1090" : "978";
    node.classList.toggle("strip--alert", !!contact.emergency);
    node.style.setProperty("--age", Math.min(contact.seen / STALE_SECONDS, 1).toFixed(2));
    node.setAttribute("aria-label",
      (contact.owner && contact.owner.tail || contact.icao)
      + ", " + (ownerLabel(contact.owner) || "owner not on file"));
    return node;
  }

  /* ---------- list assembly ---------- */

  function visibleContacts() {
    var term = state.filter.trim().toLowerCase();
    var list = state.contacts.filter(function (contact) {
      var owner = contact.owner || {};
      if (state.namedOnly && !owner.owner) return false;
      if (!term) return true;
      return [owner.owner, owner.tail, contact.callsign, contact.icao,
              owner.model, owner.manufacturer, owner.city]
        .join(" ").toLowerCase().indexOf(term) >= 0;
    });

    var comparators = {
      range: function (a, b) {
        return (a.distance_nm === null ? 9999 : a.distance_nm)
             - (b.distance_nm === null ? 9999 : b.distance_nm);
      },
      altitude: function (a, b) {
        return (b.altitude || 0) - (a.altitude || 0);
      },
      arrival: function (a, b) { return b.first_seen - a.first_seen; }
    };
    return list.sort(comparators[state.sort] || comparators.range);
  }

  function render() {
    var list = visibleContacts();
    var live = {};
    var fragmentOrder = [];

    list.forEach(function (contact) {
      live[contact.icao] = true;
      fragmentOrder.push(ensureStrip(contact));
    });

    Object.keys(nodes).forEach(function (icao) {
      if (!live[icao]) {
        if (nodes[icao].parentNode) nodes[icao].parentNode.removeChild(nodes[icao]);
        delete nodes[icao];
      }
    });

    fragmentOrder.forEach(function (node, index) {
      if (el.strips.children[index] !== node) {
        el.strips.insertBefore(node, el.strips.children[index] || null);
      }
    });

    var hasAny = state.contacts.length > 0;
    if (!hasAny) {
      el.empty.textContent = "Nothing in range. The receiver is running; no aircraft are being heard right now.";
      el.empty.hidden = false;
    } else if (list.length === 0) {
      el.empty.textContent = "No strips match this filter. Clear it to see all "
        + state.contacts.length + " contacts.";
      el.empty.hidden = false;
    } else {
      el.empty.hidden = true;
    }

    updateCounts();
  }

  function updateCounts() {
    var on1090 = 0, on978 = 0, named = 0;
    state.contacts.forEach(function (contact) {
      if (contact.bands.indexOf("1090") >= 0) on1090++;
      if (contact.bands.indexOf("978") >= 0) on978++;
      if (contact.owner && contact.owner.owner) named++;
    });
    el.count1090.textContent = on1090;
    el.count978.textContent = on978;
    el.countNamed.textContent = named;
  }

  /* ---------- registration card ---------- */

  function entry(label, value) {
    return '<div class="entry"><dt>' + label + "</dt><dd>"
         + escapeHtml(value || "") + "</dd></div>";
  }

  function openCard(icao) {
    var contact = state.contacts.filter(function (c) { return c.icao === icao; })[0];
    var owner = (contact && contact.owner) || {};

    el.cardTail.textContent = owner.tail || icao.toUpperCase();
    el.cardBody.innerHTML = buildCardBody(contact, owner);
    el.card.hidden = false;
    el.card.querySelector(".card__close").focus();

    fetch("/api/registration/" + encodeURIComponent(icao))
      .then(function (response) { return response.json(); })
      .then(function (fresh) {
        if (el.card.hidden) return;
        el.cardTail.textContent = fresh.tail || icao.toUpperCase();
        el.cardBody.innerHTML = buildCardBody(contact, fresh);
      })
      .catch(function () { /* the strip data already on screen is enough */ });
  }

  function buildCardBody(contact, owner) {
    var html = "";

    html += '<section class="block"><h3 class="block__title">Registered to</h3><div class="grid">'
      + entry("Name", ownerLabel(owner))
      + entry("Registrant", owner.owner_type)
      + entry("Also on file", owner.other_names ? titleCase(owner.other_names) : "")
      + entry("Address", owner.street ? titleCase(owner.street) : "")
      + entry("City", placeText(owner))
      + entry("Postal code", owner.postal)
      + "</div></section>";

    html += '<section class="block"><h3 class="block__title">Airframe</h3><div class="grid">'
      + entry("Manufacturer", owner.manufacturer ? titleCase(owner.manufacturer) : "")
      + entry("Model", owner.model)
      + entry("Year built", owner.year_built)
      + entry("Serial", owner.serial)
      + entry("Type", owner.aircraft_type)
      + entry("Engine", owner.engine_type)
      + entry("Engines", owner.engines)
      + entry("Seats", owner.seats)
      + "</div></section>";

    html += '<section class="block"><h3 class="block__title">Certificate</h3><div class="grid">'
      + entry("Status", owner.status)
      + entry("Issued", owner.cert_issued)
      + entry("Expires", owner.expires)
      + entry("Source", owner.source)
      + "</div></section>";

    if (contact) {
      html += '<section class="block"><h3 class="block__title">Heard now</h3><div class="grid">'
        + entry("ICAO address", contact.icao.toUpperCase())
        + entry("Band", contact.bands.join(" + ") + " MHz")
        + entry("Callsign", contact.callsign)
        + entry("Squawk", contact.squawk)
        + entry("Altitude", altitudeText(contact))
        + entry("Ground speed", contact.ground_speed
            ? Math.round(contact.ground_speed) + " kt" : "")
        + entry("Track", contact.track !== null && contact.track !== undefined
            ? Math.round(contact.track) + "\u00b0" : "")
        + entry("Range", rangeText(contact))
        + entry("Bearing", bearingText(contact))
        + entry("Signal", contact.rssi !== null && contact.rssi !== undefined
            ? contact.rssi.toFixed(1) + " dBFS" : "")
        + entry("Position", contact.latitude
            ? contact.latitude.toFixed(4) + ", " + contact.longitude.toFixed(4) : "")
        + entry("Last message", contact.seen + " s ago")
        + "</div></section>";
    }

    if (owner.resolution === "derived") {
      html += '<p class="notice">This tail number comes from the ICAO address itself, '
        + 'not from the registry file. The FAA assigns US addresses in a fixed pattern, so '
        + 'the tail is reliable even though no owner record matched. Rebuild the registry '
        + 'to pick up recent registrations.</p>';
    } else if (owner.resolution === "unknown") {
      html += '<p class="notice">No registry entry, and the address falls outside the US '
        + 'block, so no tail can be derived. '
        + (owner.block ? "The address is allocated to " + escapeHtml(owner.block) + ". " : "")
        + 'Adding the OpenSky dataset to the build extends coverage beyond the US.</p>';
    } else if (owner.deregistered) {
      html += '<p class="notice">This registration has been cancelled. The airframe may have '
        + 'been re-registered, exported, or scrapped; the details above are from the last '
        + 'record on file.</p>';
    }

    return html;
  }

  function closeCard() {
    el.card.hidden = true;
  }

  /* ---------- polling ---------- */

  function poll() {
    fetch("/api/contacts")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        state.contacts = data.contacts;
        state.receiver = data.receiver;
        render();
        describeStatus(data);
      })
      .catch(function () {
        el.empty.textContent = "Lost the server. Check that server.py is still running, "
          + "then reload.";
        el.empty.hidden = false;
        el.feedNote.textContent = "server unreachable";
      });
  }

  function describeStatus(data) {
    state.configured = data.configured;

    if (data.receiver && data.receiver.lat !== null) {
      el.receiverNote.textContent = data.receiver.lat.toFixed(3) + ", "
        + data.receiver.lon.toFixed(3);
    } else if (!data.configured) {
      el.receiverNote.textContent = "no receiver set";
    } else {
      el.receiverNote.textContent = "receiver position unset";
    }

    el.registryNote.textContent = data.registry_size
      ? data.registry_size.toLocaleString() + " registrations on file"
      : "no registry yet \u2014 build it in Setup";

    var feeds = data.feeds || {};
    el.feedNote.textContent = Object.keys(feeds).map(function (band) {
      return band + " " + (feeds[band] === "ok" ? "ok" : "down");
    }).join("  \u00b7  ");

    if (!data.contacts.length && !data.configured) {
      el.empty.textContent = "No receiver yet. Open Setup and enter the address "
        + "of the Pi running PiAware.";
      el.empty.hidden = false;
    }

    // First run: bring up Setup once, rather than showing an empty board.
    if (!state.promptedSetup && (!data.configured || !data.registry_size)) {
      state.promptedSetup = true;
      openSetup();
    }
  }

  /* ---------- setup ---------- */

  function openSetup() {
    el.setup.hidden = false;
    fetch("/api/settings")
      .then(function (response) { return response.json(); })
      .then(function (data) {
        el.hostInput.value = data.values.piaware_host || "";
        el.latInput.value = data.values.receiver_lat === null
          ? "" : data.values.receiver_lat;
        el.lonInput.value = data.values.receiver_lon === null
          ? "" : data.values.receiver_lon;
        el.quit.hidden = !data.app_mode;
        describeRegistry(data.registry);
        describeFeeds(data.feeds);
      })
      .catch(function () {
        el.hostResult.textContent = "Could not read the current settings.";
        el.hostResult.dataset.tone = "bad";
      });
  }

  function describeFeeds(feeds) {
    if (!feeds) return;
    var found = [];
    if (feeds["1090"]) found.push("1090 MHz");
    if (feeds["978"]) found.push("978 UAT");
    if (found.length) {
      el.hostResult.textContent = "Receiving " + found.join(" and ") + ".";
      el.hostResult.dataset.tone = "ok";
    }
  }

  function describeRegistry(registry) {
    if (!registry) return;
    if (registry.running) {
      el.registryState.textContent = "Building.";
      el.buildProgress.hidden = false;
      el.buildBar.style.width = registry.percent + "%";
      el.buildMessage.textContent = registry.message;
      el.buildMessage.dataset.tone = "ok";
      el.buildRegistry.disabled = true;
      pollBuild();
      return;
    }

    el.buildRegistry.disabled = false;
    el.buildProgress.hidden = true;

    if (registry.error) {
      el.buildMessage.textContent = registry.error;
      el.buildMessage.dataset.tone = "bad";
    } else if (registry.count) {
      el.buildMessage.textContent = "Built "
        + registry.count.toLocaleString() + " aircraft.";
      el.buildMessage.dataset.tone = "ok";
    } else {
      el.buildMessage.textContent = "";
    }

    el.registryState.textContent = registry.size
      ? registry.size.toLocaleString() + " aircraft on file."
      : "Not built yet. Without it you still get tail numbers for US "
        + "aircraft, but no owner names.";
  }

  var buildTimer = null;
  function pollBuild() {
    if (buildTimer) return;
    buildTimer = setInterval(function () {
      fetch("/api/registry/state")
        .then(function (response) { return response.json(); })
        .then(function (registry) {
          if (!registry.running) {
            clearInterval(buildTimer);
            buildTimer = null;
          }
          describeRegistry(registry);
        })
        .catch(function () {
          clearInterval(buildTimer);
          buildTimer = null;
        });
    }, 700);
  }

  function saveSetup() {
    el.saveSetup.disabled = true;
    post("/api/settings", {
      piaware_host: el.hostInput.value,
      receiver_lat: el.latInput.value.trim(),
      receiver_lon: el.lonInput.value.trim()
    }).then(function (data) {
      el.saveSetup.disabled = false;
      describeFeeds(data.feeds);
      if (!data.configured) {
        el.hostResult.textContent = "Saved, but neither band answered at that "
          + "address. Check the Pi is reachable and PiAware is running.";
        el.hostResult.dataset.tone = "bad";
      } else {
        el.setup.hidden = true;
      }
    }).catch(function () {
      el.saveSetup.disabled = false;
      el.hostResult.textContent = "Could not save those settings.";
      el.hostResult.dataset.tone = "bad";
    });
  }

  el.openSetup.addEventListener("click", openSetup);

  Array.prototype.forEach.call(el.setup.querySelectorAll("[data-setup-close]"),
    function (target) {
      target.addEventListener("click", function () { el.setup.hidden = true; });
    });

  el.testHost.addEventListener("click", function () {
    el.testHost.disabled = true;
    el.hostResult.textContent = "Looking for a feed.";
    el.hostResult.dataset.tone = "ok";
    post("/api/probe", { piaware_host: el.hostInput.value })
      .then(function (data) {
        el.testHost.disabled = false;
        var found = [];
        if (data.result["1090"]) found.push("1090 MHz");
        if (data.result["978"]) found.push("978 UAT");
        if (found.length) {
          el.hostResult.textContent = "Found " + found.join(" and ")
            + ". Save to start using it.";
          el.hostResult.dataset.tone = "ok";
        } else {
          el.hostResult.textContent = "Nothing answered there. Check the "
            + "address, and that the PiAware web page loads in a browser.";
          el.hostResult.dataset.tone = "bad";
        }
      })
      .catch(function () {
        el.testHost.disabled = false;
        el.hostResult.textContent = "The test could not run.";
        el.hostResult.dataset.tone = "bad";
      });
  });

  el.buildRegistry.addEventListener("click", function () {
    el.buildRegistry.disabled = true;
    el.buildProgress.hidden = false;
    el.buildBar.style.width = "0%";
    el.buildMessage.textContent = "Starting.";
    el.buildMessage.dataset.tone = "ok";
    post("/api/registry/build", { opensky: el.wantOpensky.checked })
      .then(function (data) { describeRegistry(data.state); pollBuild(); })
      .catch(function () {
        el.buildRegistry.disabled = false;
        el.buildMessage.textContent = "Could not start the build.";
        el.buildMessage.dataset.tone = "bad";
      });
  });

  el.saveSetup.addEventListener("click", saveSetup);

  el.quit.addEventListener("click", function () {
    post("/api/quit").then(function () {
      document.body.innerHTML = '<p class="bay__empty">Strip Bay has stopped. '
        + "You can close this window.</p>";
    }).catch(function () {
      document.body.innerHTML = '<p class="bay__empty">Strip Bay has stopped. '
        + "You can close this window.</p>";
    });
  });

  /* ---------- wiring ---------- */

  el.filter.addEventListener("input", function () {
    state.filter = el.filter.value;
    render();
  });

  el.namedOnly.addEventListener("change", function () {
    state.namedOnly = el.namedOnly.checked;
    render();
  });

  Array.prototype.forEach.call(document.querySelectorAll(".seg"), function (button) {
    button.addEventListener("click", function () {
      Array.prototype.forEach.call(document.querySelectorAll(".seg"), function (other) {
        other.classList.toggle("is-on", other === button);
      });
      state.sort = button.dataset.sort;
      render();
    });
  });

  Array.prototype.forEach.call(el.card.querySelectorAll("[data-close]"), function (target) {
    target.addEventListener("click", closeCard);
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      if (!el.card.hidden) closeCard();
      else if (!el.setup.hidden) el.setup.hidden = true;
    }
    var typing = /^(INPUT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (event.key === "/" && !typing) {
      event.preventDefault();
      el.filter.focus();
    }
  });

  poll();
  setInterval(poll, POLL_MS);
})();
