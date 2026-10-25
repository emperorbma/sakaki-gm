# Item archetypes and instances

Status: agreed direction, not implemented. Common items should be **instances** (copies) of an **archetype**. Unique artifacts may exist **only as instances**, with no archetype.

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
| **Archetype** | The reusable definition: name, base description, tags, default rules. What the Items tab calls the **catalog** today is this idea. |
| **Instance** | A concrete copy in the object graph (character inventory, place, bag, or map drop). Play state and divergent flavor hang here. |
| **Catalog (UI alias)** | Current desk label for the archetype list. Keep or rename in the UI later; the model word is archetype. |

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
- Holds the reusable name, base description, tags (including container), and default rules text.
- Never appears in a character inventory as the contained object for ordinary gear.
- Unique artifacts **may omit** an archetype entirely.
- The Items tab continues to edit archetypes (today’s “catalog”).

### Owned instance (required wherever a copy exists)

- A distinct `objects` row that a `contains` link points at — from a character inventory container **or** from a place / map location / chest.
- Suggested id prefix: `item-instance:{uuid}` (containers may keep `inventory-container:{uuid}`).
- Suggested link: `instance_of` from instance → archetype when an archetype exists.
- Instance `properties` (or dedicated fields) hold per-copy state that is not just quantity on the link: charges spent, renamed title, unique description overrides, enchantments, artifact-only rules text.
- Link properties still hold `quantity` (for stackable commons that have not diverged), `attuned`, and `equipment_location`.
- Moving a copy (pick up / drop) retargets or recreates the `contains` edge to the **same instance** so flavor travels with the object.

### Unique artifacts

- Created as instances with **no** `instance_of` (or with `instance_of` absent and a flag such as `unique: true` on the instance).
- Full mechanical text lives on the instance description/properties.
- May still be listed in Documents or notes for lore; the owned object is the authority for play state.
- Do not force an archetype “just so the Items tab has something to drag.”

### Stackable commons vs divergent copies

- **Non-divergent** (coins, rations, identical bolts): one instance row per archetype per container/place is enough; keep `quantity` on the contains link. Shared archetype text is the whole story.
- **Divergent** (enchanted blade, renamed staff, spent-charge wand): split into (or keep as) its own instance row with overrides on the instance. Do not leave that state on the archetype or on a shared stack.
- Default for mundane gear: one instance per archetype per inventory container or place, quantity on the link (same UX as today, different graph target). Split only when play gives that copy a history of its own.

## Ask GM and proposals

- Scene gear and inventory proposals should cite **instance** `object_id`s (and optional `record_id` from scratch).
- `spend_use` should record the instance id, not only an archetype / catalog id.
- Search tools may return archetypes; inspect of an owned item should return the instance, with archetype text merged or linked when `instance_of` exists.

## Migration sketch

1. For every `contains` link whose `to_object_id` is `object_type='item'` and is not already an instance (character inventory **or** place/map containment), create `item-instance:{…}`, copy name/description from the archetype row, add `instance_of` → archetype id, retarget the contains link to the instance, preserve link properties.
2. Leave existing catalog rows in place as archetypes.
3. Mark known one-offs (or a new “unique / no archetype” create path) by clearing or never creating `instance_of`.
4. Update Items UI: list = archetypes (catalog alias); character inventory and place contents always edit instances; allow “create unique item” and “drop / pick up” without rewriting the archetype.
5. Update `set_character_items` / `save_character_inventory` / GM `inventory_*` proposals to create or address instances; place drops should move or spawn instances, not link places straight to catalog rows.

## Leave out for now

- A second item database or JSON inventory blob on the character sheet.
- Embeddings or fuzzy “same item” matching.
- Automatic detection of which current catalog rows are unique artifacts; prefer an explicit unique-create path plus optional manual “detach from archetype” later.

## Relationship to containers

Keep the existing container-instance pattern. After this change, plain items and containers both follow “owned object in the graph,” with an optional archetype behind them.
