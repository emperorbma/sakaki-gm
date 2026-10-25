# Item archetypes and instances

Status: implementing. Owned loadouts migrate to **instances** (copies) of an **archetype** on schema v15. Unique artifacts may exist **only as instances**, with no archetype.

## Why instances

The main play reason is **location and divergence**. When something is dropped on a map (or left in a room, chest, or corpse), that physical copy should keep its own flavor and history even while other copies of the same kind float around elsewhere.

- A dropped shortsword on the shrine floor is *this* sword: nicked, blood-stained, or recently enchanted — not every shortsword in the world.
- Another shortsword in a PC pack can stay a plain archetype copy. Changing the floor one must not rewrite the pack one.
- Mundane stacks often **never** diverge: coins, rations, bolts. Those stay stackable (quantity on the link) and share archetype text forever.
- Copies **do** diverge when play modifies them: enchantments, renamed titles, spent charges, unique descriptions, damage that matters later. At that point they need a distinct instance row (or an already-distinct instance gets its own properties).

Archetypes answer “what kind of thing is this?” Instances answer “which physical copy is here, and what has happened to it?”

## Terminology

| Term | Meaning |
|------|---------|
| **Archetype** | The reusable definition: name, base description, tags, default rules. Desk tab: **Archetypes**. |
| **Instance** | A concrete copy in the object graph (character inventory, place, bag, or map drop). Play state and divergent flavor hang here. |
| **Catalog (legacy alias)** | Older name for the archetype list; UI now says Archetypes. |

## Problem with the current model

Today the Items tab is a campaign-wide catalog of `object_type='item'` rows. A character “owns” a common item by a `contains` link from their inventory tree **directly to that catalog row**. Quantity, attunement, and equipment location live on the link.

That means:

- Two characters can both contain the same `catalog-item:…` id — they share one definition object, which is fine as an archetype, but the owned thing is not a copy.
- One character cannot hold two distinct copies of the same archetype (UI and save paths reject a second link to the same catalog id).
- A drop on a place that points at the same catalog row cannot keep independent flavor from inventory copies of that kind.
- Per-copy description, charges, or enchantments have nowhere to live except the shared catalog description or ad-hoc character notes.
- Unique relics (Lunar Crown, Serpentine Lunar Echo Ward, and similar) are stored as ordinary catalog items. Their identity is the catalog row itself.

Containers are already closer to the desired shape: adding a container-tagged catalog item creates an `inventory-container:…` instance with an `instance_of` link back to the template. Plain items never get that treatment.

## Target model

Keep the object graph. Split **archetype** from **owned instance**.

### Archetype (optional)

- Remains an `objects` row with `object_type='item'` for now (or a clearer kind such as `item_archetype` if a migration prefers renaming).
- Holds the reusable name, base description, tags (including container / weapon / armor / food, etc.), default rules text, and **default property sets** for those kinds. Those defaults apply to every linked instance for any property the instance has **not** overridden.
- Never appears in a character inventory as the contained object for ordinary gear.
- Unique artifacts **may omit** an archetype entirely.
- The Archetypes tab edits these definitions (formerly “Item Catalog”).

### Owned instance (required wherever a copy exists)

- A distinct `objects` row that a `contains` link points at — from a character inventory container **or** from a place / map location / chest.
- Suggested id prefix: `item-instance:{uuid}` (containers may keep `inventory-container:{uuid}`).
- Suggested link: `instance_of` from instance → archetype when an archetype exists.
- Instance `properties` hold per-copy state and **overrides** (plus any instance-only fields): charges spent, renamed title, unique description, enchantments, artifact-only rules text, and kind-specific override keys (weapon, armor, food, container, …). Unset keys still resolve from the archetype.
- Link properties still hold `quantity` (for stackable commons that have not diverged), `attuned`, and `equipment_location`.
- Moving a copy (pick up / drop) retargets or recreates the `contains` edge to the **same instance** so flavor travels with the object.

### Unique artifacts

- Created as instances with **no** `instance_of` (or with `instance_of` absent and a flag such as `unique: true` on the instance).
- Full mechanical text lives on the instance description/properties.
- May still be listed in Documents or notes for lore; the owned object is the authority for play state.
- Do not force an archetype “just so the Items tab has something to drag.”
- **Item customization utility:** Archetypes tab → **Customize owned copies…** (and **Detach all copies** on a selected archetype). Edit one owned instance’s name/description; **Detach (make unique)** bakes resolved values and clears `instance_of`; **Attach to archetype** re-links and keeps only true overrides. Non-uniques diverge a copy while defaults keep flowing for unset keys; uniques (or reattached copies) are first-class.

### Stackable commons vs divergent copies

- **Non-divergent** (coins, rations, identical bolts): one instance row per archetype per container/place is enough; keep `quantity` on the contains link. Shared archetype text is the whole story.
- **Divergent** (enchanted blade, renamed staff, spent-charge wand): split into (or keep as) its own instance row with overrides on the instance. Do not leave that state on the archetype or on a shared stack.
- Default for mundane gear: one instance per archetype per inventory container or place, quantity on the link (same UX as today, different graph target). Split only when play gives that copy a history of its own.

## Ask GM and proposals

The model does **not** need the archetype/instance split. Prompts and tool results should show the **practical result** only: what is here, what it is called, how it reads now (including any local flavor or enchantment), quantity, and usable state. Do not teach `instance_of`, catalog vs copy, or migration vocabulary in the GM prompt.

Under the hood:

- Scene gear, place contents, and inventory proposals address the concrete copy (stable `object_id` / scratch `record_id`).
- `spend_use` and `inventory_*` proposals bind to that copy id, never to a bare archetype id when an owned/dropped copy exists.
- When packing text for the model, **resolve** archetype defaults with instance overrides (including kind property sets) into one practical description. The reply cites that resolved object; the app maps the citation back to the instance row.

## Extended property sets

Instances can carry **extended information** beyond name and free text. The existing case is **container** behavior: a container-tagged archetype seeds an inventory-container instance (`instance_of` + object properties / contains graph). The same pattern generalizes to other kinds.

### Resolution rule

**Resolved value = instance override if present, otherwise the archetype default.** The archetype applies to every **non-overridden** property. Clearing an override (removing the key) returns that property to the live archetype value — instances do not freeze a full private copy of the set at spawn unless a field is explicitly overridden.

- Unique artifacts with no `instance_of` have no archetype layer; their stored properties are the whole result.
- Nested kind blocks follow the same rule key-by-key (e.g. override only `weapon.damage`; keep archetype `weapon.range`).

### Where defaults and overrides live

- **Archetype** holds the **default property set(s)** for its relevant types — e.g. `container`, `weapon`, `armor`, `food` (and later others as needed). Tags or an explicit kind list on the archetype say which sets apply.
- **Instance** stores only overrides and instance-only state (spent charges, local enchantment, renamed title). Play that changes “this copy” writes an override; play that changes “all shortswords” edits the archetype.
- Keep **contains-link** properties for placement facts (`quantity`, `attuned`, `equipment_location`). Keep **object** properties for what the thing *is* and *does* (damage die, AC bonus, spoilage, capacity, charges).
- Examples of set content (illustrative, not a frozen schema yet):
  - **container** — capacity / role (already partly present via tags + inventory-container instances).
  - **weapon** — damage, properties (finesse, reach), range, proficiency group.
  - **armor** — category, AC formula, stealth disadvantage, strength requirement.
  - **food** — portions, restorative effect, spoilage or duration notes.
- An archetype may declare **more than one** set when that makes sense (a flask that is both container and food vessel; a shield with armor and weapon-ish bash notes). Resolved views expose whichever sets the archetype declares, with instance overrides applied.
- Ask GM still only sees that **resolved** practical result, not which keys came from archetype vs override.

### Query / pack efficiency

Exact packer details can wait for implementation. The cheap path is enough for Ask GM:

- Resolve only the **rows already selected** for the scene pack, place contents, inventory list, or an `inspect_object` hit — not the whole catalog.
- For each selected instance with `instance_of`, load that one archetype (or batch-fetch the small set of archetype ids in the pack) and merge overrides in process memory before serializing prompt text.
- Cache archetype rows for the turn or scratch lifetime if the same template appears on many copies (coins, identical bolts).
- Do not push default→override vocabulary into the model; the merge stays in Sakaki before `build_messages` / tool results.

## Migration sketch

1. For every `contains` link whose `to_object_id` is `object_type='item'` and is not already an instance (character inventory **or** place/map containment), create `item-instance:{…}`, set name/description only when they already differ from the archetype (otherwise leave unset so defaults apply), add `instance_of` → archetype id, retarget the contains link to the instance, preserve link properties.
2. Leave existing catalog rows in place as archetypes; promote today’s container tag into the typed property-set pattern without breaking current inventory-container rows.
3. Mark known one-offs (or a new “unique / no archetype” create path) by clearing or never creating `instance_of`; unique instances store their full property sets directly (no override layer).
4. Update Items UI: list = archetypes (catalog alias); edit default property sets by kind on the archetype; instance editors show resolved values and only persist overrides; allow “create unique item” and “drop / pick up” without rewriting the archetype.
5. Update `set_character_items` / `save_character_inventory` / GM `inventory_*` proposals to create or address instances; place drops should move or spawn instances, not link places straight to catalog rows. Resolved reads (scene pack, inspect) always merge archetype + overrides before the model sees them.

## Leave out for now

- A second item database or JSON inventory blob on the character sheet.
- Embeddings or fuzzy “same item” matching.
- Automatic detection of which current catalog rows are unique artifacts; prefer an explicit unique-create path plus optional manual “detach from archetype” later (via the customization utility).
- A full rules engine for every weapon/armor/food field; ship the property-set slots and seed container first, then grow the schemas as UI and proposals need them.
- Snapshotting archetype values onto the instance at create time as a private full copy; keep live default→override resolution instead.
- Full typed property-set editors inside the customize dialog (name/description + detach/reattach ship first).

## Relationship to containers

Containers are the first typed property set, not a separate inventory model. After this change, plain items and containers both follow “owned object in the graph,” with optional archetype defaults and instance overrides behind them.
