# Item definitions and instances

Status: agreed direction, not implemented. Common items should be **instances** (copies) of a catalog definition. Unique artifacts may exist **only as instances**, with no shared catalog row.

## Problem with the current model

Today the Items tab is a campaign-wide catalog of `object_type='item'` rows. A character “owns” a common item by a `contains` link from their inventory tree **directly to that catalog row**. Quantity, attunement, and equipment location live on the link.

That means:

- Two characters can both contain the same `catalog-item:…` id — they share one definition object, which is fine for the template, but the owned thing is not a copy.
- One character cannot hold two distinct copies of the same template (UI and save paths reject a second link to the same catalog id).
- Per-copy description, charges, or enchantments have nowhere to live except the shared catalog description or ad-hoc character notes.
- Unique relics (Lunar Crown, Serpentine Lunar Echo Ward, and similar) are stored as ordinary catalog items. Their identity is the catalog row itself.

Containers are already closer to the desired shape: adding a container-tagged catalog item creates an `inventory-container:…` instance with an `instance_of` link back to the template. Plain items never get that treatment.

## Target model

Keep the object graph. Split **definition** from **owned instance**.

### Catalog definition (optional)

- Remains an `objects` row with `object_type='item'` (or a clearer kind such as `item_template` if a migration prefers renaming).
- Holds the reusable name, base description, tags (including container), and default rules text.
- Never appears in a character inventory as the contained object for ordinary gear.
- Unique artifacts **may omit** a catalog row entirely.

### Owned instance (required in inventory)

- A distinct `objects` row that the inventory `contains` link points at.
- Suggested id prefix: `item-instance:{uuid}` (containers may keep `inventory-container:{uuid}`).
- Suggested link: `instance_of` from instance → catalog definition when a definition exists.
- Instance `properties` (or dedicated fields) hold per-copy state that is not just quantity on the link: charges spent, renamed title, unique description overrides, artifact-only rules text.
- Link properties still hold `quantity` (for stackable commons), `attuned`, and `equipment_location`.

### Unique artifacts

- Created as instances with **no** `instance_of` (or with `instance_of` absent and a flag such as `unique: true` on the instance).
- Full mechanical text lives on the instance description/properties.
- May still be listed in Documents or notes for lore; the owned object is the authority for play state.
- Do not force a catalog entry “just so the Items tab has something to drag.”

### Stackable commons

- Multiple quantity of the same template on one character can remain one instance row with `quantity` on the contains link, **or** become multiple instance rows when copies must diverge (different charges, named blades, etc.).
- Default for mundane gear: one instance per template per inventory container, quantity on the link (same UX as today, different graph target).

## Ask GM and proposals

- Scene gear and inventory proposals should cite **instance** `object_id`s (and optional `record_id` from scratch).
- `spend_use` should record the instance id, not only a catalog id.
- Catalog search tools may return definitions; inspect of an owned item should return the instance, with definition text merged or linked when `instance_of` exists.

## Migration sketch

1. For every `contains` link whose `to_object_id` is `object_type='item'` and is not already an instance, create `item-instance:{…}`, copy name/description from the catalog row, add `instance_of` → catalog id, retarget the contains link to the instance, preserve link properties.
2. Leave catalog rows in place as definitions.
3. Mark known one-offs (or a new “unique / no catalog” create path) by clearing or never creating `instance_of`.
4. Update Items UI: catalog = definitions; character inventory always edits instances; allow “create unique item” without a catalog pick.
5. Update `set_character_items` / `save_character_inventory` / GM `inventory_*` proposals to create or address instances.

## Leave out for now

- A second item database or JSON inventory blob on the character sheet.
- Embeddings or fuzzy “same item” matching.
- Automatic detection of which current catalog rows are unique artifacts; prefer an explicit unique-create path plus optional manual “detach from catalog” later.

## Relationship to containers

Keep the existing container-instance pattern. After this change, plain items and containers both follow “owned object in the graph,” with an optional definition behind them.
