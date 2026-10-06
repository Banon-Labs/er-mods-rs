-- Moonrithyll, Carian Knight (NpcName 143200). Her map entry, m61_47_44_00 part c0000_9003:
-- NPCParamID 524320082, ThinkParamID 524320000, CharaInitID 2024320. Spawn her with
--   respawn {"npcParam": 524320082, "think": 524320000, "charaInit": 2024320}
--
-- Her think row runs the same logic (10000) and battle goal (29999) as Moongrum's, and _lab.lua
-- lists her in LAB_TARGET_THINK, so the follow, hearing and patrol mods drive her as they do him.
--
-- Her CharaInitParam row gives her the Carian Knight Helm (980000). Protector 10000 is the bare
-- head (EquipParamProtector 10000: headEquip 1, equipModelId 0).
lab_equip(524320000, "head", 10000)

-- The "Ordinary Bean" build (er-build-planner ?b=98f687a96d43b1), less its Albinauric Mask so she
-- stays bare-headed. Armament ids are the affinity row plus the upgrade level: Bloodfiend's Arm
-- 12530000 + Blood 1100 + 25, Miséricorde 1030000 + Lightning 600 + 25, Spiralhorn Shield
-- 30190000 + Magic 800 + 25. The planner's armament positions 0 and 2 are right hand 1 and 3, 3 is
-- left hand 1. Each weapon carries the build's Ash of War (EquipParamGem): Poisonous Mist 22800,
-- Bloodhound's Step 80100, Carian Retaliation 30500.
lab_equip(524320000, "right1", 12531125, 22800)
lab_equip(524320000, "right3", 1030625, 80100)
lab_equip(524320000, "left1", 30190825, 30500)
-- The build's face (its `faceData`), applied over her FaceParam 2024320 face at creation.
lab_face(524320000, "4641434504000000200100006600000071000000000000000300000009000000000000000300000002000000FF8000008B948F8D66898C6B734988877E83B5BE69FF778E44009D0095360079770049AF4E62AF0A8C7900516B9778FFB2FF4AB993853B7FCF736828A59190908000000000808080808000000000000000000080808080000000008080808B8080808080808080808080808080808080808080808080808080808080808080007FFFFFF8DAF8DA94816FC8FFFF64281E19323A0D0D005B31230022050500466E6E46CC9E8F4EB91C8C4730188000D24F4A33FFFF33DC00646464CDCDB980FFFF33DCFF646464CDCDB9804F4A33306EB877756A46EB784F4A33308C5E4F4A334E231E000000000000000000000000000000000000")
lab_equip(524320000, "chest", 5010100)
lab_equip(524320000, "hands", 5010200)
lab_equip(524320000, "legs", 5010300)
