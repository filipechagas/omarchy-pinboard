function startsLikeUrl(value) {
  var text = String(value || "").trim()
  if (text === "" || text.length > 65536 || /[\s\x00-\x1f\x7f]/.test(text)) return false

  var candidate = text
  if (candidate.indexOf("//") === 0) candidate = "https:" + candidate

  var explicit = candidate.match(/^([A-Za-z][A-Za-z0-9+.-]*):\/\//)
  if (explicit) {
    if (explicit[1].toLowerCase() !== "http" && explicit[1].toLowerCase() !== "https")
      return false
  } else {
    if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(candidate)
        && !/^[^/:]+:\d+(?:\/|$)/.test(candidate)) return false
    candidate = "https://" + candidate
  }

  var authority = candidate.replace(/^https?:\/\//i, "").split(/[/?#]/)[0]
  if (authority === "") return false
  var host = authority.split("@").pop()
  if (host.charAt(0) === "[") {
    var close = host.indexOf("]")
    if (close <= 1) return false
    if (!isIpv6Literal(host.slice(1, close))) return false
    var suffix = host.slice(close + 1)
    if (suffix === "") return true
    if (!/^:\d+$/.test(suffix)) return false
    var ipv6Port = Number(suffix.slice(1))
    return ipv6Port >= 0 && ipv6Port <= 65535
  }

  if (host.indexOf(":") !== host.lastIndexOf(":")) return false
  var colon = host.lastIndexOf(":")
  if (colon !== -1) {
    if (!/^\d+$/.test(host.slice(colon + 1))) return false
    var port = Number(host.slice(colon + 1))
    if (port < 0 || port > 65535) return false
    host = host.slice(0, colon)
  }
  return host !== "" && !/^\.+$/.test(host)
}

function isIpv4Literal(value) {
  var parts = String(value || "").split(".")
  if (parts.length !== 4) return false
  for (var i = 0; i < parts.length; i++) {
    if (!/^\d{1,3}$/.test(parts[i])) return false
    if (parts[i].length > 1 && parts[i].charAt(0) === "0") return false
    var octet = Number(parts[i])
    if (octet < 0 || octet > 255) return false
  }
  return true
}

function isIpv6Literal(value) {
  var text = String(value || "")
  if (text === "" || !/^[0-9A-Fa-f:.]+$/.test(text) || text.indexOf(":::") !== -1)
    return false
  if (text.charAt(0) === ":" && text.indexOf("::") !== 0) return false
  if (text.charAt(text.length - 1) === ":" && text.lastIndexOf("::") !== text.length - 2)
    return false

  var compression = text.indexOf("::")
  if (compression !== -1 && text.indexOf("::", compression + 2) !== -1) return false

  var parts = text.split(":")
  var sections = 0
  for (var i = 0; i < parts.length; i++) {
    if (parts[i] === "") continue
    if (parts[i].indexOf(".") !== -1) {
      if (i !== parts.length - 1 || !isIpv4Literal(parts[i])) return false
      sections += 2
    } else {
      if (!/^[0-9A-Fa-f]{1,4}$/.test(parts[i])) return false
      sections++
    }
  }
  return compression === -1 ? sections === 8 : sections < 8
}

function splitTags(value) {
  var text = String(value || "").trim()
  if (text === "") return []
  return text.split(/\s+/).filter(function(tag) { return tag !== "" })
}

function uniqueTags(tags) {
  var seen = {}
  var result = []
  for (var i = 0; i < tags.length; i++) {
    var tag = String(tags[i] || "").trim()
    var key = ":" + tag.toLowerCase()
    if (tag === "" || seen[key]) continue
    seen[key] = true
    result.push(tag)
  }
  return result
}

function mergeTag(input, tag) {
  return uniqueTags(splitTags(input).concat([tag])).join(" ")
}

function mergeSuggestions(input, suggestions) {
  var recommended = suggestions && suggestions.recommended ? suggestions.recommended : []
  var popular = suggestions && suggestions.popular ? suggestions.popular : []
  return uniqueTags(splitTags(input).concat(recommended).concat(popular)).join(" ")
}

function suggestionTags(suggestions) {
  if (!suggestions) return []
  return uniqueTags((suggestions.recommended || []).concat(suggestions.popular || []))
}

function autocomplete(input, suggestions, existingTags) {
  var text = String(input || "")
  if (text.trim() === "" || /\s$/.test(text)) return []

  var parts = text.split(/\s+/)
  var partial = String(parts[parts.length - 1] || "").trim()
  if (partial === "") return []

  var partialLower = partial.toLowerCase()
  var alreadyUsed = {}
  for (var i = 0; i < parts.length - 1; i++) alreadyUsed[":" + parts[i].toLowerCase()] = true

  var pool = suggestionTags(suggestions).concat(existingTags || [])
  var uniquePool = uniqueTags(pool)
  var result = []
  for (var j = 0; j < uniquePool.length && result.length < 8; j++) {
    var candidate = uniquePool[j]
    var lower = candidate.toLowerCase()
    if (lower.indexOf(partialLower) !== 0 || lower === partialLower || alreadyUsed[":" + lower]) continue
    result.push(candidate)
  }
  return result
}

function completeTag(input, tag) {
  var text = String(input || "")
  var parts = text.split(/\s+/)
  if (parts.length === 0) parts = [""]
  parts[parts.length - 1] = String(tag || "")
  return parts.join(" ") + " "
}

function validateForm(url, title, notes, tags) {
  var cleanUrl = String(url || "").trim()
  var cleanTitle = String(title || "").trim()
  var noteText = String(notes || "")
  var tagList = splitTags(tags)

  if (cleanUrl === "") return "URL is required."
  if (!startsLikeUrl(cleanUrl)) return "Enter a valid HTTP or HTTPS URL."
  if (cleanTitle === "") return "Title is required."
  if (String(title || "").length > 255) return "Title must be 255 characters or fewer."
  if (noteText.length > 65536) return "Notes must be 65,536 characters or fewer."
  if (tagList.length > 100) return "Pinboard accepts at most 100 tags."
  for (var i = 0; i < tagList.length; i++) {
    if (tagList[i].length > 255) return "Each tag must be 255 characters or fewer."
    if (tagList[i].indexOf(",") !== -1) return "Tags cannot contain commas."
  }
  return ""
}

function queueCount(items, status) {
  var count = 0
  var list = items || []
  for (var i = 0; i < list.length; i++) {
    if (String(list[i].status || "pending") === status) count++
  }
  return count
}
