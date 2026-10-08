-- Leo's three brothers, summoned together as one squad. Leo (?b=d1366ca1f24c85, Nagakiba) is the
-- player, so he is not summoned. Their brain is brain_turtles.lua.
--
-- All three run Moongrum's think row (NpcThinkParam 523590100: logic 10000, battle goal 29999), so
-- the brain needs one id and the lab's follow team and home apply. They are told apart by summon
-- order instead: spawn-npc.js applies a "think.N" gear key to its Nth summon only. Spawn them with
--   respawn {"npcParam": 523590024, "think": 523590100, "charaInit": 23590, "count": 3}
--
-- Gear is each planner build's equipped set and nothing else, so there is nothing to swap to:
--   0 Raph    ?b=10a41c37574781  Parrying Dagger, Keen +5, Parry
--   1 Donnie  ?b=4f8d9635d54d5f  Staff of the Avatar +2 (somber; the build's regular +5)
--   2 Mikey   ?b=fdbb6222e440ee  Flail, Keen +5; its skill has no Ash of War, so no gem
-- Every one carries the Great Turtle Shell +5 with No Skill (EquipParamGem 30900) on the left, is
-- bare but for the Soiled Loincloth (EquipParamProtector 5070300; 10000/10100/10200 are the bare
-- head, body and arms), and has Unarmed (110000) in every other weapon slot. Armament ids are the
-- affinity row plus the level, read from the planner's own item table: Keen is +200.
--
-- Faces: each build's sliders encoded by scripts/planner-face-hex.py over Moonrithyll's face buffer.
-- They differ only in the blindfold colour (accessoriesColour; accessoriesModelId 10 is the
-- blindfold): Raph [170,30,30], Donnie [110,30,170], Mikey [71,40,10].

TURTLE_THINK = 523590100
lab_brain(TURTLE_THINK, "turtles")

local function turtle(index, weapon, gem, face)
  local key = TURTLE_THINK .. "." .. index
  lab_equip(key, "right1", weapon, gem)
  lab_equip(key, "left1", 31140005, 30900)
  lab_equip(key, "right2", 110000)
  lab_equip(key, "right3", 110000)
  lab_equip(key, "left2", 110000)
  lab_equip(key, "left3", 110000)
  lab_equip(key, "head", 10000)
  lab_equip(key, "chest", 10100)
  lab_equip(key, "hands", 10200)
  lab_equip(key, "legs", 5070300)
  lab_face(key, face)
end

turtle(0, 1020205, 30200, "46414345040000002001000096000000000000000000000000000000000000000A000000000000000000000096BE00000073F38986339E57E90E1BFF06F1FFB3FFC34E0DFF00E53A0EDF00020000201231C0FB060DEB11F6FF5CF500F7E30904A41CD70A790005F600E3F7E4800000000080808080800000000000000000008080808000000000808080808080808080808080808080808080808080808080808080808080808080808080004E64325044504440791AFF00000A3A0D0D00FFFFFF005B3123000522220047301800F5DAD0808080FF808080800000E8E6DA1A0F05FF3C1A0F05FFFFFF8A1A0F05FF3C1A0F05FFFFFF8AE8E6DAFF0023E8E6DAFF0023B9B9B9FF0023E8E6DAAA1E1E000000000000000000000000000000000000")
turtle(1, 23070002, nil, "46414345040000002001000096000000000000000000000000000000000000000A000000000000000000000096BE00000073F38986339E57E90E1BFF06F1FFB3FFC34E0DFF00E53A0EDF00020000201231C0FB060DEB11F6FF5CF500F7E30904A41CD70A790005F600E3F7E4800000000080808080800000000000000000008080808000000000808080808080808080808080808080808080808080808080808080808080808080808080004E64325044504440791AFF00000A3A0D0D00FFFFFF005B3123000522220047301800F5DAD0808080FF808080800000E8E6DA1A0F05FF3C1A0F05FFFFFF8A1A0F05FF3C1A0F05FFFFFF8AE8E6DAFF0023E8E6DAFF0023B9B9B9FF0023E8E6DA6E1EAA000000000000000000000000000000000000")
turtle(2, 13010205, nil, "46414345040000002001000096000000000000000000000000000000000000000A000000000000000000000096BE00000073F38986339E57E90E1BFF06F1FFB3FFC34E0DFF00E53A0EDF00020000201231C0FB060DEB11F6FF5CF500F7E30904A41CD70A790005F600E3F7E4800000000080808080800000000000000000008080808000000000808080808080808080808080808080808080808080808080808080808080808080808080004E64325044504440791AFF00000A3A0D0D00FFFFFF005B3123000522220047301800F5DAD0808080FF808080800000E8E6DA1A0F05FF3C1A0F05FFFFFF8A1A0F05FF3C1A0F05FFFFFF8AE8E6DAFF0023E8E6DAFF0023B9B9B9FF0023E8E6DA47280A000000000000000000000000000000000000")

-- Their switchable Ashes of War, from the planner builds (2026-10-06 revisions: Raph rev 5, Mikey
-- rev 4; Donnie's staff has none). EquipParamGem: Storm Blade 21000, Thunderbolt 21600, Chilling
-- Mist 22700, Poisonous Mist 22800, Parry 30200, Bloodhound's Step 80100, Blinkbolt 413000.
lab_arts(TURTLE_THINK .. ".0", "30200,21600,22700,22800,21000,413000,80100")
lab_arts(TURTLE_THINK .. ".2", "-1,22800,80100,21600,413000")

-- Their names, written into each summon's character name (lab_name).
lab_name(TURTLE_THINK .. ".0", "Raphael")
lab_name(TURTLE_THINK .. ".1", "Donatello")
lab_name(TURTLE_THINK .. ".2", "Michelangelo")
