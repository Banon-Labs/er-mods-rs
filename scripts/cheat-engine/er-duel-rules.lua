-- er-duel-rules.lua -- the competitive duel ruleset referee for Cheat Engine, a port of
-- scripts/frida/er-duel-rules.js (itself a port of crates/er-duel-rules-core).
--
-- Every rule break, by anyone in the session, is appended to er-duel-rules-breaks.txt beside
-- eldenring.exe (the same file and line as the DLL and the Frida agent write) and listed in a
-- small window that pops up over Cheat Engine, newest first. Everything else it notices --
-- players as they are judged, skills used, status every few seconds -- goes to
-- er-duel-rules-ce.log in the same folder, which is the file to send back.
--
-- This file is not opened by hand: scripts/er-duel-rules-gen.py --write puts it inside
-- er-duel-rules.CT, whose one entry runs it when ticked. `lua scripts/test-er-duel-rules-ce.lua`
-- runs its rule half on any machine, with no game and no Cheat Engine.
--
-- What it touches: it reads memory, and places one hook in the game's SpEffect apply (the
-- table's Auto Assembler half) that writes every effect that took hold into a ring in memory
-- Cheat Engine allocated, so a heal or a flask that lasts one frame is still seen. Nothing in
-- the game's own data is written. It refuses to start on any game build but the one it was
-- written against.
--
-- What only your own copy sees: the BMP manual attack aiming setting and an unlocked frame rate
-- are not sent to other players, so each player's copy judges only that player.

local M = {}

-- --- BEGIN GENERATED (scripts/er-duel-rules-gen.py --write) ---
M.T = {affinity = {COLD = 900, LIGHTNING = 600}, bannedArmor = {{210000, "Briar Helm"}, {210100, "Briar Armor"}, {210200, "Briar Gauntlets"}, {210300, "Briar Greaves"}, {211100, "Briar Armor (Altered)"}, {5160000, "Rakshasa Helm"}, {5160100, "Rakshasa Armor"}, {5160200, "Rakshasa Gauntlets"}, {5160300, "Rakshasa Greaves"}}, bannedTears = {{11002, "Crimson Crystal Tear", "Heal"}, {11003, "Crimson Crystal Tear", "Heal"}, {11007, "Crimson Bubbletear", "Heal"}, {11009, "Crimsonburst Crystal Tear", "Heal"}, {11012, "Winged Crystal Tear", "Named"}, {11013, "Thorny Cracked Tear", "Damage"}, {11014, "Spiked Cracked Tear", "Damage"}, {11015, "Windy Crystal Tear", "Named"}, {11018, "Leaden Hardtear", "Named"}, {11020, "Crimsonwhorl Bubbletear", "Heal"}, {11021, "Strength-knot Crystal Tear", "Stat"}, {11022, "Dexterity-knot Crystal Tear", "Stat"}, {11023, "Intelligence-knot Crystal Tear", "Stat"}, {11024, "Faith-knot Crystal Tear", "Stat"}, {11026, "Stonebarb Cracked Tear", "Named"}, {11028, "Flame-Shrouding Cracked Tear", "Damage"}, {11029, "Magic-Shrouding Cracked Tear", "Damage"}, {11030, "Lightning-Shrouding Cracked Tear", "Damage"}, {11031, "Holy-Shrouding Cracked Tear", "Damage"}, {2011010, "Crimsonburst Dried Tear", "Heal"}, {2011050, "Bloodsucking Cracked Tear", "Damage"}, {2011070, "Deflecting Hardtear", "Named"}}, bodyBuffs = {{1586, "Contagious Fury"}, {1588, "Contagious Fury"}, {1640, "Holy Ground"}, {1650, "Endure"}, {1655, "Endure"}, {1730, "Golden Vow"}, {1768, "Rallying Standard"}, {1836, "Last Rites"}, {1850, "Oath of Vengeance"}, {1851, "Oath of Vengeance"}, {1870, "Shared Order"}, {3910, "Spellproof Dried Liver"}, {3920, "Fireproof Dried Liver"}, {3930, "Lightningproof Dried Liver"}, {3950, "Exalted Flesh"}, {3953, "Pickled Turtle Neck"}, {3960, "Immunizing Cured Meat"}, {3961, "Invigorating Cured Meat"}, {3962, "Clarifying Cured Meat"}, {3963, "Dappled Cured Meat"}, {19980, "Rock Heart"}, {19981, "Priestess Heart"}, {500138, "Phantom Great Rune"}, {500139, "Phantom Great Rune"}, {500820, "Boiled Crab"}, {500830, "Boiled Prawn"}, {501180, "Holyproof Dried Liver"}, {501290, "Starlight Shards"}, {501310, "Immunizing White Cured Meat"}, {501320, "Invigorating White Cured Meat"}, {501330, "Clarifying White Cured Meat"}, {501340, "Dappled White Cured Meat"}, {502100, "Soft Cotton"}, {503355, "Baldachin's Blessing"}, {503361, "Radiant Baldachin's Blessing"}, {503500, "Uplifting Aromatic"}, {503501, "Uplifting Aromatic"}, {503520, "Ironjar Aromatic"}, {503550, "Bloodboil Aromatic"}, {1413000, "Terra Magica"}, {1467000, "Unseen Form"}, {1467002, "Unseen Form"}, {1605000, "Flame, Grant Me Strength"}, {1606000, "Flame, Protect Me"}, {1627000, "Black Flame's Protection"}, {1633000, "Barrier of Gold"}, {1634000, "Protection of the Erdtree"}, {1645000, "Flame Fortification"}, {1646000, "Magic Fortification"}, {1647000, "Lightning Fortification"}, {1648000, "Divine Fortification"}, {1649000, "Lord's Divine Fortification"}, {1651000, "Assassin's Approach"}, {1660000, "Golden Vow"}, {1697100, "Dragonbolt Blessing"}, {1733000, "Howl of Shabriri"}, {1790300, "Golden Lightning Fortification"}, {20000851, "Flare, O Serpent"}, {20000856, "Flare, O Serpent"}, {20000980, "Light"}, {20500910, "Lulling Branch"}, {20501100, "Dragon Communion Flesh"}, {20501105, "Dragonscale Flesh"}, {20501110, "Fingerprint Nostrum"}, {20501120, "Spellproof Pickled Liver"}, {20501130, "Fireproof Pickled Liver"}, {20501140, "Lightningproof Pickled Liver"}, {20501150, "Holyproof Pickled Liver"}, {20501160, "Opaline Pickled Liver"}, {20501170, "Well-Pickled Turtle Neck"}, {20501201, "Scorpion Stew"}, {20501206, "Gourmet Scorpion Stew"}, {20501210, "Sacred Bloody Flesh"}, {20501212, "Sacred Bloody Flesh"}, {20502100, "Ancient Dragon's Blessing"}, {20503170, "Golden Vow"}, {21490000, "Mantle of Thorns"}, {21691000, "Dragonbolt of Florissax"}}, ceruleanFlask = {501050, 501051, 501052, 501053, 501054, 501055, 501056, 501057, 501058, 501059, 501060, 501061, 501062}, chillingMistBuffs = {826, 828}, fanDaggerBullets = {10175000}, greaseBuffs = {3140, 3144, 3150, 3152, 3154, 3156, 3160, 3161, 3162, 3163, 3165, 3166, 3167, 3168, 3170, 3171, 3172, 3173, 3175, 3177, 3179, 3181, 3185, 3186, 3187, 3188, 3190, 3192, 3194, 3196, 3260, 3262, 3310, 3312, 3314, 3316, 102310, 102312, 102314, 102316, 501690, 501691, 20501400, 20501401, 20501402, 20501403, 20501410, 20501411, 20501412, 20501413, 20501420, 20501421, 20501422, 20501423, 20501430, 20501431, 20501432, 20501433, 20501480, 20501481, 20501620, 20501622}, heals = {{1641, "Holy Ground"}, {3360, "Warming Stone"}, {501235, "Raw Meat Dumpling"}, {503311, "Frenzyflame Stone"}, {1642000, "Urgent Heal"}, {1642100, "Heal"}, {1642101, "Heal"}, {1642200, "Great Heal"}, {1642201, "Great Heal"}, {1642300, "Lord's Heal"}, {1642301, "Lord's Heal"}, {1642400, "Erdtree Heal"}, {1642401, "Erdtree Heal"}, {1643000, "Blessing's Boon"}, {1643001, "Blessing's Boon"}, {1643100, "Blessing of the Erdtree"}, {1643101, "Blessing of the Erdtree"}, {1685000, "Bestial Vitality"}, {20500901, "Blessing of Marika"}, {20501202, "Scorpion Stew"}, {20501207, "Gourmet Scorpion Stew"}, {20503310, "Sunwarmth Stone"}, {21640000, "Heal from Afar"}, {21640001, "Heal from Afar"}, {21667000, "Minor Erdtree"}}, parrySkills = {{92, "Parry"}, {93, "Buckler Parry"}, {95, "Carian Retaliation"}, {96, "Storm Wall"}, {97, "Golden Parry"}, {99, "Thops's Barrier"}}, poisonGrease = {3175, 3177, 3179, 3181}, regenEquipment = {{"Armor", 690000, "Royal Remains Helm"}, {"Armor", 690100, "Royal Remains Armor"}, {"Armor", 690200, "Royal Remains Gauntlets"}, {"Armor", 690300, "Royal Remains Greaves"}, {"Shield", 32140000, "Icon Shield"}, {"Talisman", 5020, "Blessed Dew Talisman"}}, rippleWeapons = {{14050000, "Ripple Blade"}, {18060000, "Ripple Crescent Halberd"}}, rotGrease = {3310, 3312, 3314, 3316}, runeArcEffects = {{490, "Rune Arc"}, {491, "Rune Arc"}, {499, "Rune Arc"}, {600, "Godrick's Great Rune"}, {601, "Godrick's Great Rune"}, {602, "Godrick's Great Rune"}, {603, "Godrick's Great Rune"}, {604, "Godrick's Great Rune"}, {605, "Godrick's Great Rune"}, {606, "Godrick's Great Rune"}, {607, "Godrick's Great Rune"}, {609, "Godrick's Great Rune"}, {610, "Radahn's Great Rune"}, {611, "Radahn's Great Rune"}, {612, "Radahn's Great Rune"}, {613, "Radahn's Great Rune"}, {614, "Radahn's Great Rune"}, {615, "Radahn's Great Rune"}, {616, "Radahn's Great Rune"}, {617, "Radahn's Great Rune"}, {619, "Radahn's Great Rune"}, {620, "Morgott's Great Rune"}, {621, "Morgott's Great Rune"}, {622, "Morgott's Great Rune"}, {623, "Morgott's Great Rune"}, {624, "Morgott's Great Rune"}, {625, "Morgott's Great Rune"}, {626, "Morgott's Great Rune"}, {627, "Morgott's Great Rune"}, {629, "Morgott's Great Rune"}, {630, "Rykard's Great Rune"}, {631, "Rykard's Great Rune"}, {632, "Rykard's Great Rune"}, {633, "Rykard's Great Rune"}, {634, "Rykard's Great Rune"}, {635, "Rykard's Great Rune"}, {636, "Rykard's Great Rune"}, {637, "Rykard's Great Rune"}, {639, "Rykard's Great Rune"}, {640, "Mohg's Great Rune"}, {641, "Mohg's Great Rune"}, {642, "Mohg's Great Rune"}, {643, "Mohg's Great Rune"}, {644, "Mohg's Great Rune"}, {645, "Mohg's Great Rune"}, {646, "Mohg's Great Rune"}, {647, "Mohg's Great Rune"}, {649, "Mohg's Great Rune"}, {650, "Malenia's Great Rune"}, {651, "Malenia's Great Rune"}, {652, "Malenia's Great Rune"}, {653, "Malenia's Great Rune"}, {654, "Malenia's Great Rune"}, {655, "Malenia's Great Rune"}, {656, "Malenia's Great Rune"}, {657, "Malenia's Great Rune"}, {659, "Malenia's Great Rune"}}, skill = {BARBARIC_ROAR = 140, BLIND_SPOT = 276, BLOODHOUNDS_STEP = 156, CHILLING_MIST = 75, DETERMINATION = 130, ENDURE = 150, FLAMING_STRIKE = 63, MULETA = 372, NEEDLE_PIERCER = 301, OATH_OF_VENGEANCE = 242, PAINFUL_STRIKE = 269, QUICKSTEP = 155, RAPTOR_OF_THE_MISTS = 157, RED_BEAR_HUNT = 275, ROYAL_KNIGHTS_RESOLVE = 131, SAVAGE_CLAWS = 274, SHRIEK_OF_SORROW = 286, SPINNING_SLASH = 3, STORMCALLER = 23, STORM_ASSAULT = 22, STORM_STOMP = 111, SWIFT_SLASH = 277, SWORD_DANCE = 24}, sleepGrease = {3150, 3152, 3154, 3156, 102310, 102312, 102314, 102316}, spellBullets = {{"Gravitational Missile", {210471000, 210471001, 210471002, 210471003, 210471004, 210471005, 210471006, 210471007, 210471050, 210471051, 210471052, 210471053, 210471054, 210471055, 210471056, 210471057, 210471058, 210471059, 210471060}}, {"Night Maiden's Mist", {10650000, 10650001, 10650002, 10650050, 10650051, 10650052}}, {"The Flame of Frenzy", {10731000, 10731001, 10731002, 10731005, 10731006, 10731010, 10731050, 10731051, 10731052, 10731055, 10731056, 10731060}}, {"Roar of Rugalea", {210680000, 210680050}}}, spiritAshTriggers = {{200000, "Black Knife Tiche"}, {201000, "Banished Knight Oleg"}, {202000, "Banished Knight Engvall"}, {203000, "Fanged Imp Ashes"}, {204000, "Latenna the Albinauric"}, {205000, "Nomad Ashes"}, {206000, "Nightmaiden & Swordstress Puppets"}, {207000, "Mimic Tear Ashes"}, {208000, "Crystalian Ashes"}, {209000, "Ancestral Follower Ashes"}, {210000, "Winged Misbegotten Ashes"}, {211000, "Albinauric Ashes"}, {212000, "Skeletal Militiaman Ashes"}, {213000, "Skeletal Bandit Ashes"}, {214000, "Oracle Envoy Ashes"}, {215000, "Putrid Corpse Ashes"}, {216000, "Depraved Perfumer Carmaan"}, {217000, "Perfumer Tricia"}, {218000, "Glintstone Sorcerer Ashes"}, {219000, "Twinsage Sorcerer Ashes"}, {220000, "Page Ashes"}, {221000, "Battlemage Hugues"}, {222000, "Clayman Ashes"}, {223000, "Cleanrot Knight Finlay"}, {224000, "Kindred of Rot Ashes"}, {225000, "Marionette Soldier Ashes"}, {226000, "Avionette Soldier Ashes"}, {227000, "Fire Monk Ashes"}, {228000, "Blackflame Monk Amon"}, {229000, "Man-Serpent Ashes"}, {230000, "Azula Beastman Ashes"}, {231000, "Kaiden Sellsword Ashes"}, {232000, "Lone Wolf Ashes"}, {233000, "Giant Rat Ashes"}, {233100, "spirit summon"}, {233200, "spirit summon"}, {234000, "Demi-Human Ashes"}, {235000, "Rotten Stray Ashes"}, {236000, "Spirit Jellyfish Ashes"}, {237000, "Warhawk Ashes"}, {238000, "Stormhawk Deenh"}, {239000, "Bloodhound Knight Floh"}, {240000, "Wandering Noble Ashes"}, {241000, "Noble Sorcerer Ashes"}, {242000, "Vulgar Militia Ashes"}, {243000, "Mad Pumpkin Head Ashes"}, {244000, "Land Squirt Ashes"}, {245000, "Miranda Sprout Ashes"}, {246000, "Soldjars of Fortune Ashes"}, {247000, "Omenkiller Rollo"}, {248000, "Greatshield Soldier Ashes"}, {249000, "Archer Ashes"}, {250000, "Godrick Soldier Ashes"}, {251000, "Raya Lucaria Soldier Ashes"}, {252000, "Leyndell Soldier Ashes"}, {253000, "Radahn Soldier Ashes"}, {254000, "Mausoleum Soldier Ashes"}, {255000, "Haligtree Soldier Ashes"}, {256000, "Ancient Dragon Knight Kristoff"}, {257000, "Redmane Knight Ogha"}, {258000, "Lhutel the Headless"}, {259000, "Nepheli Loux Puppet"}, {260000, "Dung Eater Puppet"}, {261000, "Finger Maiden Therolina Puppet"}, {262000, "Dolores the Sleeping Arrow Puppet"}, {263000, "Jarwight Puppet"}, {20200000, "Curseblade Meera"}, {20201000, "Bloodfiend Hexer's Ashes"}, {20202000, "Gravebird Ashes"}, {20203000, "Fire Knight Hilde"}, {20204000, "Spider Scorpion Ashes"}, {20205000, "Inquisitor Ashes"}, {20206000, "Demi-Human Swordsman Yosh"}, {20207000, "Messmer Soldier Ashes"}, {20208000, "Black Knight Commander Andreas"}, {20209000, "Black Knight Captain Huw"}, {20210000, "Bigmouth Imp Ashes"}, {20211000, "Man-Fly Ashes"}, {20212000, "Taylew the Golem Smith"}, {20213000, "Divine Bird Warrior Ornis"}, {20214000, "Horned Warrior Ashes"}, {20215000, "Ancient Dragon Florissax"}, {20217000, "Fingercreeper Ashes"}, {20218000, "Fire Knight Queelign"}, {20219000, "Swordhand of Night Jolán"}, {20220000, "Jolán and Anna"}, {20223000, "spirit summon"}, {20224000, "spirit summon"}, {20225000, "spirit summon"}, {20226000, "spirit summon"}, {20227000, "spirit summon"}, {20228000, "spirit summon"}, {20229000, "spirit summon"}, {20230000, "spirit summon"}}, statusSkills = {{52, "Ice Spear"}, {72, "Blood Blade"}, {75, "Chilling Mist"}, {76, "Poisonous Mist"}, {110, "Hoarfrost Stomp"}, {136, "Seppuku"}, {168, "Zamor Ice Storm"}, {174, "Death Flare"}, {216, "Reduvia Blood Blade"}, {223, "Mists of Slumber"}, {225, "Wolf's Assault"}, {234, "Bloodboon Ritual"}, {243, "Ice Lightning Sword"}, {248, "Ghostflame Ignition"}, {250, "Frenzyflame Thrust"}, {283, "Divine Beast Frost Stomp"}, {311, "Mists of Eternal Sleep"}, {334, "Feeble Lord's Frenzied Flame"}, {358, "Deadly Poison Spray"}, {364, "Ghostflame Call"}, {365, "Rancor Slash"}, {366, "Rancor Slash"}}, swordDanceFastClips = {40000, 40005, 40010, 40015, 45800, 45805, 45810, 45815}, talisman = {ALL_CRUCIBLES = 8240, CRUCIBLE_FEATHER = 4070, FINE_CRUCIBLE_FEATHER = 8010, GODFREY_ICON = 3090, RED_FEATHER = 2040, RELLANAS_CAMEO = 8220, SHARD_OF_ALEXANDER = 1231, SPEAR = 2060, TWO_HANDED_SWORD = 8040, WARRIOR_JAR_SHARD = 1230}, tearEffects = {{3505, 11002, "Crimson Crystal Tear", "Heal"}, {3512, 11007, "Crimson Bubbletear", "Heal"}, {3513, 11007, "Crimson Bubbletear", "Heal"}, {3515, 11021, "Strength-knot Crystal Tear", "Stat"}, {3516, 11022, "Dexterity-knot Crystal Tear", "Stat"}, {3517, 11023, "Intelligence-knot Crystal Tear", "Stat"}, {3518, 11024, "Faith-knot Crystal Tear", "Stat"}, {3519, 11018, "Leaden Hardtear", "Named"}, {3550, 11013, "Thorny Cracked Tear", "Damage"}, {3551, 11013, "Thorny Cracked Tear", "Damage"}, {3552, 11013, "Thorny Cracked Tear", "Damage"}, {3553, 11013, "Thorny Cracked Tear", "Damage"}, {3554, 11013, "Thorny Cracked Tear", "Damage"}, {3555, 11013, "Thorny Cracked Tear", "Damage"}, {3556, 11013, "Thorny Cracked Tear", "Damage"}, {3557, 11013, "Thorny Cracked Tear", "Damage"}, {3558, 11013, "Thorny Cracked Tear", "Damage"}, {3559, 11013, "Thorny Cracked Tear", "Damage"}, {3560, 11013, "Thorny Cracked Tear", "Damage"}, {3561, 11013, "Thorny Cracked Tear", "Damage"}, {3580, 11020, "Crimsonwhorl Bubbletear", "Heal"}, {3581, 11020, "Crimsonwhorl Bubbletear", "Heal"}, {3582, 11020, "Crimsonwhorl Bubbletear", "Heal"}, {3583, 11020, "Crimsonwhorl Bubbletear", "Heal"}, {3584, 11020, "Crimsonwhorl Bubbletear", "Heal"}, {3585, 11020, "Crimsonwhorl Bubbletear", "Heal"}, {3586, 11020, "Crimsonwhorl Bubbletear", "Heal"}, {3587, 11020, "Crimsonwhorl Bubbletear", "Heal"}, {102000, 2011070, "Deflecting Hardtear", "Named"}, {511009, 11009, "Crimsonburst Crystal Tear", "Heal"}, {511012, 11012, "Winged Crystal Tear", "Named"}, {511014, 11014, "Spiked Cracked Tear", "Damage"}, {511015, 11015, "Windy Crystal Tear", "Named"}, {511016, 11015, "Windy Crystal Tear", "Named"}, {511026, 11026, "Stonebarb Cracked Tear", "Named"}, {511028, 11028, "Flame-Shrouding Cracked Tear", "Damage"}, {511029, 11029, "Magic-Shrouding Cracked Tear", "Damage"}, {511030, 11030, "Lightning-Shrouding Cracked Tear", "Damage"}, {511031, 11031, "Holy-Shrouding Cracked Tear", "Damage"}, {20511010, 2011010, "Crimsonburst Dried Tear", "Heal"}, {20511011, 2011010, "Crimsonburst Dried Tear", "Heal"}, {20511050, 2011050, "Bloodsucking Cracked Tear", "Damage"}, {20511051, 2011050, "Bloodsucking Cracked Tear", "Damage"}}, weapon = {BACKHAND_BLADE = 64500000, CLAWS_OF_NIGHT = 22500000, CLEANROT_KNIGHTS_SWORD = 5010000, CLUB = 11010000, IDUS_SWORD = 67530000, LEONTIELS_GREATSWORD = 3560000, MILADY = 67500000, MOHGWYNS_SACRED_SPEAR = 17010000, PATA = 21510000, RAPTOR_TALONS = 22030000, REVERSE_BLADED_SWORD = 64530000, SHAMSHIR = 7030000, SMITHSCRIPT_CIRQUE = 64510000, SMITHSCRIPT_DAGGER = 63500000, SMITHSCRIPT_SHIELD = 30510000, SPREAD_CROSSBOW = 43510000, STAR_FIST = 21080000, STONE_CLUB = 11140000, SWORD_LANCE = 3500000, WAKIZASHI = 1100000}, wepType = {AXE = 17, BACKHAND_BLADE = 92, CLAW = 37, CURVED_SWORD = 9, DAGGER = 1, FIST = 35, GREATSHIELD = 69, GREAT_SPEAR = 28, HEAVY_THRUSTING_SWORD = 16, LIGHT_GREATSWORD = 93, MEDIUM_SHIELD = 67, SMALL_SHIELD = 65, THRUSTING_SHIELD = 90, THRUSTING_SWORD = 15, TWINBLADE = 14}}
M.PARAM_FIELDS = {CalcCorrectGraph = {fields = {adjPt_maxGrowVal0 = {40, "f32"}, adjPt_maxGrowVal1 = {44, "f32"}, adjPt_maxGrowVal2 = {48, "f32"}, adjPt_maxGrowVal3 = {52, "f32"}, adjPt_maxGrowVal4 = {56, "f32"}, stageMaxGrowVal0 = {20, "f32"}, stageMaxGrowVal1 = {24, "f32"}, stageMaxGrowVal2 = {28, "f32"}, stageMaxGrowVal3 = {32, "f32"}, stageMaxGrowVal4 = {36, "f32"}, stageMaxVal0 = {0, "f32"}, stageMaxVal1 = {4, "f32"}, stageMaxVal2 = {8, "f32"}, stageMaxVal3 = {12, "f32"}, stageMaxVal4 = {16, "f32"}}, index = 30, struct = "CACL_CORRECT_GRAPH_ST"}, EquipParamAccessory = {fields = {refCategory = {49, "u8"}, refId = {4, "s32"}, residentSpEffectId1 = {76, "s32"}, residentSpEffectId2 = {80, "s32"}, residentSpEffectId3 = {84, "s32"}, residentSpEffectId4 = {88, "s32"}, weight = {12, "f32"}}, index = 2, struct = "EQUIP_PARAM_ACCESSORY_ST"}, EquipParamProtector = {fields = {residentSpEffectId = {40, "s32"}, residentSpEffectId2 = {44, "s32"}, residentSpEffectId3 = {48, "s32"}, weight = {36, "f32"}}, index = 1, struct = "EQUIP_PARAM_PROTECTOR_ST"}, EquipParamWeapon = {fields = {isEnhance = {262, "bit", 7}, physGuardCutRate = {52, "f32"}, reinforceTypeId = {218, "s16"}, spEffectBehaviorId0 = {68, "s32"}, spEffectBehaviorId1 = {72, "s32"}, spEffectBehaviorId2 = {76, "s32"}, weight = {16, "f32"}, wepType = {422, "u16"}}, index = 0, struct = "EQUIP_PARAM_WEAPON_ST"}, ReinforceParamWeapon = {fields = {physicsGuardCutRate = {44, "f32"}}, index = 4, struct = "REINFORCE_PARAM_WEAPON_ST"}, SpEffectParam = {fields = {addEndureStatus = {800, "s8"}, equipWeightChangeRate = {228, "f32"}, spCategory = {318, "u16"}}, index = 15, struct = "SP_EFFECT_PARAM_ST"}}
-- --- END GENERATED ---

local T, PARAM_FIELDS = M.T, M.PARAM_FIELDS

-- The build this script is pinned to; every RVA and offset below was measured against it.
M.BUILD = { label = 'ELDEN RING 1.17.1 (eldenring.exe 2.7.1.0)', timeDateStamp = 0x6a96b418 }

M.RVA = {
    gameDataMan = 0x3d61f98,
    worldChrMan = 0x3d69ff8,
    soloParamRepository = 0x3d85f58,
    bulletManager = 0x3d667a8,
    flipper = 0x458db58,
}

local OFF = {
    wcmPlayerChrSet = 0x10ee0,
    wcmMainPlayer = 0x1e508,
    chrSetCapacity = 0x10,
    chrSetEntries = 0x18,
    chrSetEntryStride = 0x10,
    playerGameData = 0x580,
    chrHandle = 0x8,
    chrSpecialEffect = 0x178,
    chrModules = 0x190,
    modData = 0x0,
    modTimeAct = 0x18,
    modRide = 0xe8,
    dataHp = 0x138,
    timeActQueue = 0x20,
    timeActEntry = 0x10,
    timeActCount = 10,
    timeActPlayTime = 0x4,
    timeActWriteIdx = 0xc0,
    rideNode = 0x10,
    rideState = 0x50,
    speHead = 0x8,
    speEntryParamId = 0x8,
    speEntryNext = 0x30,
    pgdLevel = 0x68,
    pgdEndurance = 0x44,
    pgdAttributes = 0x3c,
    pgdRuneArcActive = 0xff,
    gdmGameSettings = 0x58,
    settingsManualAttackAiming = 0x16,
    flipperTargetSpf = 0x1c,
    flipperMeasuredFps = 0x2b8,
    pgdName = 0x9c,
    pgdNameChars = 17,
    pgdSize = 0xaf0,
    pgdChrAsm = 0x31c,
    pgdPhysick = 0x694,
    asmArmStyle = 0x8,
    asmLeftSlot = 0xc,
    asmRightSlot = 0x10,
    asmParamIds = 0x7c,
    gdmRemoteList = 0x10,
    gdmRemoteStates = 0x20,
    bmHead = 0x8,
    bulletOwner = 0xb40,
    bulletParamIds = { 0xdd0, 0xda0, 0xe00 },
    bulletNext = 0xe18,
    sprHolders = 0x80,
    holderSize = 0x48,
    holderResCaps = 0x8,
    paramResCap = 0x80,
    fd4ParamResCapData = 0x80,
}
M.OFF = OFF
local REMOTE_FULLY_SYNCED = 0xf
local REMOTE_SLOTS = 5
local WALK_LIMIT = 512

-- ==========================================================================================
-- Rules: a port of the Frida agent's pure half. Nothing here touches the game.
-- ==========================================================================================
local NO_ITEM = -1
local RUNE_LEVEL = 125
local CERULEAN_FLASK_LIMIT = 2
local SHIELD_PHYS_GUARD_CAP = 90.0
-- referee.rs REARM_MS: a lasting break seen again within this long is the same break; an event
-- is never merged.
local REARM_MS = 1000
-- A weapon buff the game did not strip within this long of its hand's weapon changing was
-- carried across (referee.rs BUFF_TRANSFER_GRACE_MS).
local BUFF_TRANSFER_GRACE_MS = 250
-- The game caps at 60; a higher target, or a measured rate this far past it, is an unlocker.
local UNLOCKED_TARGET_FPS = 61
local UNLOCKED_MEASURED_FPS = 65
-- Time a player has from entering the match or respawning to light a Rune Arc in Better
-- Multiplayer (referee.rs RUNE_ARC_GRACE_MS).
local RUNE_ARC_GRACE_MS = 20000
local CATEGORY_RIGHT_WEAPON_BUFF = 162
local CATEGORY_LEFT_WEAPON_BUFF = 163
local ARM = { EMPTY = 0, ONE = 1, LEFT_BOTH = 2, RIGHT_BOTH = 3 }
M.ARM = ARM

-- Single precision, as the game computes equip load (JS Math.fround).
local function f32(x)
    return (string.unpack('<f', string.pack('<f', x)))
end
M.f32 = f32
local LIGHT_LOAD_CEILING = f32(0.3)

local RULE_TEXT = {
    RuneLevel = 'Rune Level: 125',
    PlayerBuff = 'Player Buffs Globally Banned',
    PassiveRegen = 'Passive tick-based HP Regen via Armour/Talismans/Shields',
    Healing = 'HP Recovery via Spells/Crystal Tears/Consumables',
    SpectralSteed = 'Spectral Steed Whistle',
    SpiritAshes = 'Spirit Ashes',
    Respec = 'Respecing or swapping character during a match is not allowed',
    SwitchingBuilds = 'Switching builds is prohibited once you enter the same world as your opponent',
    ArmorSwapPoiseReset = 'Armor swap poise reset',
    BuffTransfer = 'Buff Transfers',
    ManualAttackAiming = 'Manual attack aiming setting must be disabled when in the BMP mod',
    UnlockedFps = 'Adhere to the Elden Ring Terms of Service (unlocked FPS)',
    RuneArc = 'Rune Arc (in Better Multiplayer every player has one active)',
    CeruleanFlasks = 'Blue flasks limited to 2',
    FanDaggers = 'Fan Daggers',
    ShieldGuard = 'Blocking With Shields above 90% physical absorption',
    RotGrease = 'Rot Greases',
    SleepGreaseRipple = 'Sleep greases on Ripple Weapons',
    PoisonGreaseShamshir = 'Poison greases on 2H Shamshir',
    WeaponBuffRestricted = 'Buff Restrictions',
    StatTear = 'All Stat Boosting Tears',
    DamageTear = 'All Damage Boosting Tears',
    NamedTear = 'Banned Crystal Tear',
    ThrustingShield = 'Thrusting Shields Class',
    PowerstanceBackhandBlade = 'PS Backhand Blade (Class)',
    SmithscriptCirque = 'Smithscript Cirque',
    TwoHandedBackhandBlade = '2h Backhand Blade',
    LightningReverseBlades = 'Lightning Infused 2h Reverse Blades',
    TwoHandedCleanrot = "2H Cleanrot Knight's Sword",
    PataNotOffhand = 'Pata only allowed as Offhand Moveset',
    ClawsOfNight = 'Claws of Night',
    SmithscriptShield = 'Smithscript Shield',
    SpreadCrossbow = 'Spread Crossbow',
    SmithscriptDagger = 'Smithscript Dagger',
    Milady = 'Milady',
    StoneClubStatus = '2H Stone Club/Club Any Status Application',
    LightningRaptorTalons = "Lightning Infused 2h Raptor's Talons",
    BannedSpell = 'Banned Spell',
    LightRoll = 'Light Roll (<30% Equip Load)',
    BannedArmor = "All Pieces of Briar or Rakshasa's armour",
    BannedAsh = 'Banned Ash of War',
    ChillingMistCold = 'Chilling Mist applied on cold infused weapons',
    SpinningSlashLightGreatsword = 'Spinning Slash on Light Greatswords',
    MuletaTalismans = 'Muleta with Ash Damage boosting talismans',
    IFrameTalisman = 'All I-Frame Affecting Talismans',
    RedFeather = 'Red Feather',
    TwoHandedSwordTalisman = 'Two-Handed Sword Tali.',
    SpearTalismanSwordLance = 'Spear Talisman on Sword Lance',
    OffhandPoisonGrease = 'Offhand drawstring poison grease',
}
M.RULE_TEXT = RULE_TEXT

local function weaponBase(id) return id - (id % 10000) end
local function weaponAffinity(id) return math.floor((id % 10000) / 100) * 100 end
local function weaponRow(id) return id - (id % 100) end
local function weaponLevel(id) return id % 100 end
M.weaponBase, M.weaponAffinity = weaponBase, weaponAffinity

local function contains(list, value)
    for _, v in ipairs(list) do
        if v == value then return true end
    end
    return false
end

-- The name a [id, name, ...] row list gives `id`, or nil.
local function lookup(rows, id)
    for _, row in ipairs(rows) do
        if row[1] == id then return row[2] end
    end
    return nil
end

local function find(rows, pred)
    for _, row in ipairs(rows) do
        if pred(row) then return row end
    end
    return nil
end

-- [swordArtsTypeNew, clip] of a skill animation, or nil.
local function skillClip(anim)
    local tae = math.floor(anim / 1000000)
    if tae >= 600 then return { tae - 600, anim % 1000000 } end
    return nil
end
M.skillClip = skillClip

-- The TimeAct anim queue is a ring of the last ten frames: each frame the game writes the current
-- animation and its play time at writeIdx and advances it. A start is where consecutive frames
-- change id or the play time falls back. `history` is {{animId, playTime}, ...} in slot order
-- (slot 0 first); `writeIdx` counts slots from 0, as the game does.
local function animStarts(last, history, writeIdx)
    local len = #history
    if not (writeIdx >= 0 and writeIdx < len) then return {}, last end
    local newest = history[((writeIdx + len - 1) % len) + 1]
    local cursor = { writeIdx = writeIdx, anim = newest[1], playTime = newest[2] }
    if not last then return {}, cursor end
    local frames = (writeIdx + len - last.writeIdx) % len
    if frames == 0 and (newest[1] ~= last.anim or newest[2] ~= last.playTime) then frames = len end
    local starts = {}
    local prevAnim, prevTime = last.anim, last.playTime
    for k = 0, frames - 1 do
        local h = history[((last.writeIdx + k) % len) + 1]
        local a, t = h[1], h[2]
        if a > 0 and (a ~= prevAnim or t < prevTime) then starts[#starts + 1] = a end
        prevAnim, prevTime = a, t
    end
    return starts, cursor
end
M.animStarts = animStarts

-- The weapon in `hand`'s selected slot; slots count from 0, as the game's ChrAsm does.
local function active(p, hand)
    local list = hand == 'left' and p.weaponsLeft or p.weaponsRight
    local slot = hand == 'left' and p.leftSlot or p.rightSlot
    local id = list[slot + 1]
    if id == nil then return NO_ITEM end
    return id
end

local function twoHanded(p)
    if p.armStyle == ARM.LEFT_BOTH then return 'left' end
    if p.armStyle == ARM.RIGHT_BOTH then return 'right' end
    return nil
end

local function twoHandedWeapon(p)
    local hand = twoHanded(p)
    if hand == nil then return NO_ITEM end
    return active(p, hand)
end
-- c0000.hks GetSwordArtInfo: a skill that is neither a shield nor an arrow-stance skill plays
-- from the two-handed weapon, or one-handed from the right.
local function nonShieldSkillWeapon(p) return active(p, twoHanded(p) or 'right') end

local function weapons(p)
    local out = {}
    for _, list in ipairs({ p.weaponsLeft, p.weaponsRight }) do
        for _, id in ipairs(list) do
            if id >= 0 then out[#out + 1] = id end
        end
    end
    return out
end

local function classOf(facts, id)
    if id < 0 then return nil end
    local w = facts.weapon(id)
    return w and w.wepType or nil
end

-- CalcCorrectGraph in single precision (facts.rs CorrectGraph::at).
local function correctGraphAt(g, x)
    local sv, gv = g.stageMaxVal, g.stageMaxGrowVal
    x = f32(math.min(x, sv[5]))
    if not (x > 0) then return gv[1] end
    local i = 0
    while i < 4 and x > sv[i + 2] do i = i + 1 end
    if i == 4 then return gv[5] end
    local lo = sv[i + 1]
    local span = f32(sv[i + 2] - sv[i + 1])
    local dg = f32(gv[i + 2] - gv[i + 1])
    local e = g.adjPtMaxGrowVal[i + 1]
    local v
    if e < 0 then
        local pw = f32(f32(f32(span - f32(x - lo)) / span) ^ (-e))
        v = f32(gv[i + 1] + f32(f32(1 - pw) * dg))
    else
        local pw = f32(f32(f32(x - lo) / span) ^ e)
        v = f32(gv[i + 1] + f32(pw * dg))
    end
    local a, b = gv[i + 1], gv[i + 2]
    return math.min(math.max(v, math.min(a, b)), math.max(a, b))
end
M.correctGraphAt = correctGraphAt

-- Burden over max load, or nil when an item has no row (facts.rs).
local function equipLoad(p, facts)
    local burden = 0
    for _, id in ipairs(weapons(p)) do
        local w = facts.weapon(id)
        if not w then return nil end
        burden = f32(burden + w.weight)
    end
    for _, id in ipairs(p.armor) do
        if id >= 0 then
            local w = facts.armorWeight(id)
            if w == nil then return nil end
            burden = f32(burden + w)
        end
    end
    for _, id in ipairs(p.talismans) do
        if id >= 0 then
            local w = facts.talismanWeight(id)
            if w == nil then return nil end
            burden = f32(burden + w)
        end
    end
    local ids, order = {}, {}
    local function add(id)
        if not ids[id] then ids[id] = true; order[#order + 1] = id end
    end
    for _, id in ipairs(p.speffects) do add(id) end
    for _, id in ipairs(p.talismans) do
        if id >= 0 then for _, e in ipairs(facts.residentEffects('talisman', id)) do add(e) end end
    end
    for _, id in ipairs(p.armor) do
        if id >= 0 then for _, e in ipairs(facts.residentEffects('armor', id)) do add(e) end end
    end
    local rate, addEnd = 1, 0
    for _, id in ipairs(order) do
        if id > 0 then
            local e = facts.effect(id)
            if e then
                if e.equipWeightRate > 0 then rate = f32(rate * e.equipWeightRate) end
                addEnd = addEnd + e.addEndurance
            end
        end
    end
    local base = facts.equipLoadBase(math.min(p.endurance + addEnd, 99))
    if base == nil then return nil end
    local max = f32(base * rate)
    local ratio = max > 0 and f32(burden / max) or 0
    return { burden = burden, max = max, ratio = ratio, light = max > 0 and ratio <= LIGHT_LOAD_CEILING }
end
M.equipLoad = equipLoad

local function buffRestrictedSetup(p, hand, facts)
    local id = active(p, hand)
    local cls = classOf(facts, id)
    if cls == nil then return nil end
    local base = weaponBase(id)
    local W, C = T.weapon, T.wepType
    if base == W.IDUS_SWORD then return 'Idus Sword' end
    if cls == C.SMALL_SHIELD or cls == C.MEDIUM_SHIELD or cls == C.GREATSHIELD or cls == C.THRUSTING_SHIELD then
        return 'Shields'
    end
    local two = twoHanded(p)
    if two == hand then
        if cls == C.THRUSTING_SWORD then return '2H Thrusting Sword' end
        if base == W.STONE_CLUB or base == W.CLUB then return '2H Stone Club/Club' end
        if base == W.SWORD_LANCE then return '2H Sword Lance' end
        if base == W.RAPTOR_TALONS then return '2H Raptors Talons' end
        if cls == C.GREAT_SPEAR and base ~= W.MOHGWYNS_SACRED_SPEAR then return '2H Greatspears' end
        if base == W.STAR_FIST then return '2H Star Fists' end
        if cls == C.TWINBLADE then return '2H Twinblades' end
        if base == W.REVERSE_BLADED_SWORD then return '2H Reverse Blades' end
        return nil
    end
    if hand == 'left' and two == nil and cls == C.DAGGER then return 'Offhand Daggers' end
    if hand == 'left' and two == nil and cls == C.AXE then return 'Offhand Hatchets' end
    if hand == 'right' and two == nil and base == W.WAKIZASHI then return 'Mainhand 1H Wakizashi' end
    return nil
end

-- rules.rs is_roll: c0000's roll animations, eight directions per block. The single-direction
-- 27000..27030 beside them are backsteps.
local function isRoll(anim)
    local inBlock = (anim >= 27100 and anim <= 27137) or (anim >= 27140 and anim <= 27143)
        or (anim >= 27500 and anim <= 27507) or (anim >= 27600 and anim <= 27607)
    return inBlock and anim % 10 <= 7
end
M.isRoll = isRoll

-- JS toFixed: a tie rounds up, where C's %.0f would round it to even.
local function fmtFloat(x, places)
    if places == 0 then return string.format('%d', math.floor(x + 0.5)) end
    return string.format('%.' .. places .. 'f', x)
end

-- Every rule `p` breaks this frame except the per-round flask count.
local function check(p, facts)
    local out = {}
    -- rules.rs violation / equipped / event: something done, what is equipped (judged only once an
    -- opponent shares the world), and one discrete act that is never merged with another.
    local function v(rule, detail) out[#out + 1] = { rule = rule, detail = detail, onEquipment = false, event = false } end
    local function eq(rule, detail) out[#out + 1] = { rule = rule, detail = detail, onEquipment = true, event = false } end
    local function ev(rule, detail) out[#out + 1] = { rule = rule, detail = detail, onEquipment = false, event = true } end
    local W, C, S, TAL = T.weapon, T.wepType, T.skill, T.talisman
    if p.level ~= 0 and p.level ~= RUNE_LEVEL then eq('RuneLevel', string.format('Rune Level %d', p.level)) end

    -- Weapons.
    local function isShield(cls)
        return cls == C.SMALL_SHIELD or cls == C.MEDIUM_SHIELD or cls == C.GREATSHIELD or cls == C.THRUSTING_SHIELD
    end
    local bannedWeapons = {
        { W.SMITHSCRIPT_CIRQUE, 'SmithscriptCirque' }, { W.CLAWS_OF_NIGHT, 'ClawsOfNight' },
        { W.SMITHSCRIPT_SHIELD, 'SmithscriptShield' }, { W.SPREAD_CROSSBOW, 'SpreadCrossbow' },
        { W.SMITHSCRIPT_DAGGER, 'SmithscriptDagger' }, { W.MILADY, 'Milady' },
    }
    for _, id in ipairs(weapons(p)) do
        local base = weaponBase(id)
        for _, b in ipairs(bannedWeapons) do
            if base == b[1] then eq(b[2], 'equipped') end
        end
        local w = facts.weapon(id)
        if w then
            if w.wepType == C.THRUSTING_SHIELD then eq('ThrustingShield', 'equipped') end
            if isShield(w.wepType) and w.wepType ~= C.THRUSTING_SHIELD and w.physGuardCut > SHIELD_PHYS_GUARD_CAP then
                eq('ShieldGuard', string.format('%d%% physical guard', math.floor(w.physGuardCut + 0.5)))
            end
            local regen = find(T.regenEquipment, function(r) return r[1] == 'Shield' and r[2] == base end)
            if regen then eq('PassiveRegen', regen[3]) end
        end
    end
    local two = twoHandedWeapon(p)
    if two >= 0 then
        local base, lightning = weaponBase(two), weaponAffinity(two) == T.affinity.LIGHTNING
        if base == W.BACKHAND_BLADE then v('TwoHandedBackhandBlade', 'two-handed') end
        if base == W.REVERSE_BLADED_SWORD and lightning then v('LightningReverseBlades', 'two-handed') end
        if base == W.RAPTOR_TALONS and lightning then v('LightningRaptorTalons', 'two-handed') end
        if base == W.CLEANROT_KNIGHTS_SWORD then v('TwoHandedCleanrot', 'two-handed') end
        if base == W.PATA then v('PataNotOffhand', 'two-handed') end
        if (base == W.STONE_CLUB or base == W.CLUB) and facts.weapon(two) and facts.weapon(two).inflictsStatus then
            v('StoneClubStatus', 'status infusion')
        end
    end
    if weaponBase(active(p, 'right')) == W.PATA then v('PataNotOffhand', 'in the right hand') end
    if p.armStyle == ARM.ONE then
        local right, left = active(p, 'right'), active(p, 'left')
        if right >= 0 and left >= 0 then
            local rc, lc = classOf(facts, right), classOf(facts, left)
            if rc ~= nil and rc == lc then
                if rc == C.BACKHAND_BLADE then v('PowerstanceBackhandBlade', 'powerstanced') end
                if weaponBase(left) == W.PATA and weaponBase(right) ~= W.PATA then v('PataNotOffhand', 'powerstanced') end
            end
        end
    end

    -- Armour and talismans.
    for _, id in ipairs(p.armor) do
        if id >= 0 then
            local name = lookup(T.bannedArmor, id)
            if name then eq('BannedArmor', name) end
            local regen = find(T.regenEquipment, function(r) return r[1] == 'Armor' and r[2] == id end)
            if regen then eq('PassiveRegen', regen[3]) end
        end
    end
    local holdsSwordLance = weaponBase(active(p, 'right')) == W.SWORD_LANCE or weaponBase(active(p, 'left')) == W.SWORD_LANCE
    local iframe = {
        [TAL.CRUCIBLE_FEATHER] = 'Crucible Feather Talisman',
        [TAL.ALL_CRUCIBLES] = 'Talisman of All Crucibles',
        [TAL.FINE_CRUCIBLE_FEATHER] = 'Fine Crucible Feather Talisman',
    }
    for _, id in ipairs(p.talismans) do
        if id >= 0 then
            if iframe[id] then eq('IFrameTalisman', iframe[id]) end
            if id == TAL.RED_FEATHER then eq('RedFeather', 'equipped') end
            if id == TAL.TWO_HANDED_SWORD then eq('TwoHandedSwordTalisman', 'equipped') end
            if id == TAL.SPEAR and holdsSwordLance then eq('SpearTalismanSwordLance', 'equipped') end
            local regen = find(T.regenEquipment, function(r) return r[1] == 'Talisman' and r[2] == id end)
            if regen then eq('PassiveRegen', regen[3]) end
        end
    end

    -- Crystal tears.
    local tearRule = { Named = 'NamedTear', Stat = 'StatTear', Damage = 'DamageTear', Heal = 'Healing' }
    for _, id in ipairs(p.physick) do
        if id >= 0 then
            local tear = find(T.bannedTears, function(r) return r[1] == id end)
            if tear then eq(tearRule[tear[3]], tear[2]) end
        end
    end

    -- SpEffects.
    local buffed = { left = false, right = false }
    for _, id in ipairs(p.speffects) do
        -- A banned tear in use: the physick put its effect on the player.
        local used = find(T.tearEffects, function(r) return r[1] == id end)
        if used then v(tearRule[used[4]], used[3]) end
        local buff = lookup(T.bodyBuffs, id)
        if buff then v('PlayerBuff', buff) end
        local heal = lookup(T.heals, id)
        if heal then v('Healing', heal) end
        if contains(T.rotGrease, id) then v('RotGrease', 'rot grease') end
        local spirit = lookup(T.spiritAshTriggers, id)
        if spirit then v('SpiritAshes', spirit) end
        local e = facts.effect(id)
        local hand = nil
        if e then
            if e.category == CATEGORY_RIGHT_WEAPON_BUFF then hand = 'right'
            elseif e.category == CATEGORY_LEFT_WEAPON_BUFF then hand = 'left' end
        end
        if hand ~= nil then
            buffed[hand] = true
            local wid = active(p, hand)
            if wid >= 0 then
                local base = weaponBase(wid)
                -- A grease takes only on an isEnhance row, so one on any other weapon was carried
                -- there.
                if contains(T.greaseBuffs, id) then
                    local wf = facts.weapon(wid)
                    if wf and wf.takesGrease == false then
                        v('BuffTransfer', string.format('grease on weapon %d, which takes none', wid))
                    end
                end
                if contains(T.sleepGrease, id) then
                    local r = lookup(T.rippleWeapons, base)
                    if r then v('SleepGreaseRipple', r) end
                end
                if contains(T.poisonGrease, id) then
                    if hand == 'left' then v('OffhandPoisonGrease', 'poison grease on the left weapon') end
                    if base == W.SHAMSHIR and twoHanded(p) == hand then v('PoisonGreaseShamshir', 'two-handed Shamshir') end
                end
                if contains(T.chillingMistBuffs, id) and weaponAffinity(wid) == T.affinity.COLD then
                    v('ChillingMistCold', 'cold weapon')
                end
            end
        end
    end
    for _, hand in ipairs({ 'left', 'right' }) do
        if buffed[hand] then
            local setup = buffRestrictedSetup(p, hand, facts)
            if setup then v('WeaponBuffRestricted', 'weapon buff on ' .. setup) end
        end
    end

    -- Skills, on use.
    local bannedSkills = {
        { S.BLOODHOUNDS_STEP, "Bloodhound's Step" }, { S.QUICKSTEP, 'Quickstep' },
        { S.RAPTOR_OF_THE_MISTS, 'Raptor of the Mists' }, { S.ENDURE, 'Endure' }, { S.OATH_OF_VENGEANCE, 'Oath of Vengeance' },
        { S.ROYAL_KNIGHTS_RESOLVE, "Royal Knight's Resolve" }, { S.DETERMINATION, 'Determination' },
        { S.FLAMING_STRIKE, 'Flaming Strike' }, { S.STORM_STOMP, 'Storm Stomp' }, { S.STORM_ASSAULT, 'Storm Assault' },
        { S.STORMCALLER, 'Stormcaller' }, { S.SWIFT_SLASH, 'Swift Slash' }, { S.BLIND_SPOT, 'Blind Spot' },
        { S.NEEDLE_PIERCER, 'Needle Piercer' }, { S.RED_BEAR_HUNT, 'Red Bear Hunt' }, { S.BARBARIC_ROAR, 'Barbaric Roar' },
        { S.SHRIEK_OF_SORROW, 'Shriek of Sorrow' }, { S.PAINFUL_STRIKE, 'Painful Strikes' }, { S.SAVAGE_CLAWS, 'Savage Claws' },
    }
    local ashTalismans = { TAL.WARRIOR_JAR_SHARD, TAL.SHARD_OF_ALEXANDER, TAL.RELLANAS_CAMEO, TAL.GODFREY_ICON }
    for _, anim in ipairs(p.newAnims) do
        local sc = skillClip(anim)
        if sc ~= nil then
            local typeNew, clip = sc[1], sc[2]
            local banned = lookup(bannedSkills, typeNew)
            if banned then ev('BannedAsh', banned) end
            local parry = lookup(T.parrySkills, typeNew)
            if parry then ev('BannedAsh', 'All Parries: ' .. parry) end
            if typeNew == S.SWORD_DANCE and contains(T.swordDanceFastClips, clip) then ev('BannedAsh', 'Fast Sword Dance') end
            if typeNew == S.SPINNING_SLASH and classOf(facts, nonShieldSkillWeapon(p)) == C.LIGHT_GREATSWORD then
                ev('SpinningSlashLightGreatsword', 'used')
            end
            -- "2H Stone Club/Club: Any Status Application", through a skill's own status.
            local statusSkill = lookup(T.statusSkills, typeNew)
            local skillBase = weaponBase(nonShieldSkillWeapon(p))
            if statusSkill and twoHanded(p) ~= nil and (skillBase == W.STONE_CLUB or skillBase == W.CLUB) then
                ev('StoneClubStatus', statusSkill)
            end
            if typeNew == S.MULETA then
                for _, id in ipairs(p.talismans) do
                    if contains(ashTalismans, id) then ev('MuletaTalismans', 'used'); break end
                end
            end
        end
    end

    -- Projectiles.
    for _, bullet in ipairs(p.bullets) do
        if contains(T.fanDaggerBullets, bullet) then v('FanDaggers', 'thrown') end
        for _, sb in ipairs(T.spellBullets) do
            if contains(sb[2], bullet) then v('BannedSpell', sb[1]) end
        end
    end

    if p.riding then v('SpectralSteed', 'riding Torrent') end
    -- Settings only this machine can read about its own player.
    local s = p.localSettings
    if s then
        if s.betterMultiplayer and s.manualAttackAiming then eq('ManualAttackAiming', 'enabled') end
        if s.targetFps > UNLOCKED_TARGET_FPS or s.measuredFps > UNLOCKED_MEASURED_FPS then
            eq('UnlockedFps', string.format('target %s fps, running at %s', fmtFloat(s.targetFps, 0), fmtFloat(s.measuredFps, 0)))
        end
    end
    -- "Light Roll (<30% Equip Load)": a roll performed at that load, each one its own break.
    local rolled = false
    for _, anim in ipairs(p.newAnims) do
        if isRoll(anim) then rolled = true end
    end
    local load = rolled and equipLoad(p, facts) or nil
    if load and load.light then ev('LightRoll', string.format('rolled at %s%% equip load', fmtFloat(load.ratio * 100, 1))) end
    return out
end
M.check = check

local ATTRIBUTE_NAMES = { 'Vigor', 'Mind', 'Endurance', 'Strength', 'Dexterity', 'Intelligence', 'Faith', 'Arcane' }

local function slotChanges(name, old, now)
    local out = {}
    for i, o in ipairs(old) do
        if o ~= now[i] then out[#out + 1] = string.format('%s %d: %d -> %d', name, i, o, now[i]) end
    end
    return out
end

local function copy(list)
    local out = {}
    for i, x in ipairs(list) do out[i] = x end
    return out
end

local function buildOf(p)
    return {
        level = p.level, attributes = copy(p.attributes), weaponsLeft = copy(p.weaponsLeft),
        weaponsRight = copy(p.weaponsRight), armor = copy(p.armor), talismans = copy(p.talismans),
        physick = copy(p.physick),
    }
end

local function append(out, list)
    for _, x in ipairs(list) do out[#out + 1] = x end
end

-- The game's flag, or any Rune Arc / Great Rune effect row.
local function runeArcActive(p)
    if p.runeArcFlag == true then return true end
    for _, id in ipairs(p.speffects) do
        if lookup(T.runeArcEffects, id) ~= nil then return true end
    end
    return false
end
M.runeArcActive = runeArcActive

-- Which breaks become a notice, the blue flask count per round, and a build that changes once two
-- players share a world (referee.rs).
local Referee = {}
Referee.__index = Referee

function Referee.new()
    return setmetatable({ lastSeen = {}, flasks = {}, alive = {}, entered = {}, hands = {}, aliveSince = {} }, Referee)
end

-- Referee::carried_buffs: the local player only.
function Referee:carriedBuffs(nowMs, p, facts)
    local out = {}
    for _, hand in ipairs({ 'left', 'right' }) do
        local cat = hand == 'left' and CATEGORY_LEFT_WEAPON_BUFF or CATEGORY_RIGHT_WEAPON_BUFF
        local buffs = {}
        for _, id in ipairs(p.speffects) do
            local e = facts.effect(id)
            if e ~= nil and e.category == cat then buffs[#buffs + 1] = id end
        end
        local weapon = active(p, hand)
        local h = self.hands[hand]
        if not h then
            h = { weapon = weapon, buffs = {}, carried = {}, changedAt = 0 }
            self.hands[hand] = h
        end
        if weapon ~= h.weapon then
            h.carried = h.buffs
            h.changedAt = nowMs
        end
        if #h.carried > 0 and nowMs - h.changedAt >= BUFF_TRANSFER_GRACE_MS then
            for _, id in ipairs(h.carried) do
                if contains(buffs, id) then
                    out[#out + 1] = { rule = 'BuffTransfer', detail = string.format('weapon buff kept across a weapon change to %d', weapon) }
                    break
                end
            end
            h.carried = {}
        end
        h.weapon = weapon
        h.buffs = buffs
    end
    return out
end

-- Referee::build_changes.
function Referee:buildChanges(p)
    if p.level == 0 then return {} end
    local now = buildOf(p)
    local entered = self.entered[p.key]
    if not entered then
        self.entered[p.key] = { build = now, armorChanged = false }
        return {}
    end
    local old = entered.build
    local out = {}
    local stats = {}
    for i, n in ipairs(ATTRIBUTE_NAMES) do
        if old.attributes[i] ~= now.attributes[i] then
            stats[#stats + 1] = string.format('%s %d -> %d', n, old.attributes[i], now.attributes[i])
        end
    end
    if old.level ~= now.level then table.insert(stats, 1, string.format('level %d -> %d', old.level, now.level)) end
    if #stats > 0 then out[#out + 1] = { rule = 'Respec', detail = table.concat(stats, ', ') } end
    local gear = slotChanges('right weapon', old.weaponsRight, now.weaponsRight)
    append(gear, slotChanges('left weapon', old.weaponsLeft, now.weaponsLeft))
    append(gear, slotChanges('talisman', old.talismans, now.talismans))
    append(gear, slotChanges('tear', old.physick, now.physick))
    local armor = slotChanges('armor', old.armor, now.armor)
    if #armor == 0 then
        if entered.armorChanged then
            entered.armorChanged = false
            out[#out + 1] = { rule = 'ArmorSwapPoiseReset', detail = 'armor taken off and put back on' }
        end
    else
        entered.armorChanged = true
        append(gear, armor)
    end
    if #gear > 0 then out[#out + 1] = { rule = 'SwitchingBuilds', detail = table.concat(gear, ', ') } end
    return out
end

local function keysOf(t)
    local out = {}
    for k in pairs(t) do out[#out + 1] = k end
    return out
end

function Referee:observe(nowMs, players, facts)
    local present = {}
    for _, p in ipairs(players) do present[p.key] = true end
    for _, k in ipairs(keysOf(self.alive)) do if not present[k] then self.alive[k] = nil end end
    for _, k in ipairs(keysOf(self.flasks)) do if not present[k] then self.flasks[k] = nil end end
    local inMatch = #players >= 2
    if inMatch then
        for _, k in ipairs(keysOf(self.entered)) do if not present[k] then self.entered[k] = nil end end
    else
        self.entered = {}
    end
    for _, k in ipairs(keysOf(self.aliveSince)) do if not present[k] then self.aliveSince[k] = nil end end
    local roundOver = false
    for _, p in ipairs(players) do
        local alive = p.hp > 0
        local was = self.alive[p.key]
        if was == true and not alive then roundOver = true end
        if alive and was ~= true then self.aliveSince[p.key] = nowMs end
        self.alive[p.key] = alive
    end
    -- A session is Better Multiplayer when the local game has the module loaded.
    local betterMultiplayer = false
    for _, p in ipairs(players) do
        if p.localSettings and p.localSettings.betterMultiplayer then betterMultiplayer = true end
    end
    if roundOver then for _, f in pairs(self.flasks) do f.used = 0 end end
    local alerts = {}
    for _, p in ipairs(players) do
        local violations = check(p, facts)
        local drinking = false
        for _, id in ipairs(p.speffects) do
            if contains(T.ceruleanFlask, id) then drinking = true end
        end
        local f = self.flasks[p.key] or { drinking = false, used = 0 }
        if drinking and not f.drinking then f.used = f.used + 1 end
        f.drinking = drinking
        self.flasks[p.key] = f
        if f.used > CERULEAN_FLASK_LIMIT then
            violations[#violations + 1] = { rule = 'CeruleanFlasks', detail = string.format('Flask of Cerulean Tears number %d this round', f.used) }
        end
        if inMatch then append(violations, self:buildChanges(p)) end
        local since = self.aliveSince[p.key]
        if inMatch and betterMultiplayer and p.hp > 0 and not runeArcActive(p) and since ~= nil and nowMs - since >= RUNE_ARC_GRACE_MS then
            violations[#violations + 1] = { rule = 'RuneArc', detail = string.format('no active Rune Arc %d s into the round', math.floor(RUNE_ARC_GRACE_MS / 1000)) }
        end
        if p.isLocal then append(violations, self:carriedBuffs(nowMs, p, facts)) end
        for _, vio in ipairs(violations) do
            if not (vio.onEquipment and not inMatch) then
                local fresh = true
                if not vio.event then
                    local key = tostring(p.key) .. '|' .. vio.rule .. '|' .. vio.detail
                    local last = self.lastSeen[key]
                    self.lastSeen[key] = nowMs
                    fresh = last == nil or nowMs - last >= REARM_MS
                end
                if fresh then
                    alerts[#alerts + 1] = { key = p.key, player = p.name, isLocal = p.isLocal, rule = vio.rule, detail = vio.detail }
                end
            end
        end
    end
    for _, k in ipairs(keysOf(self.lastSeen)) do
        if nowMs - self.lastSeen[k] >= REARM_MS then self.lastSeen[k] = nil end
    end
    return alerts
end

function Referee:flasksUsed(key)
    local f = self.flasks[key]
    return f and f.used or 0
end
M.Referee = Referee

-- The player as listed: the character, and for whoever runs this copy their Steam name beside it,
-- so a file read later says which account broke the rule; "(you)" only when Steam could not be
-- asked (feed.rs player_label).
local function playerLabel(player, isLocal, steamName)
    if not isLocal then return player end
    if steamName and steamName ~= '' then return player .. ' (' .. steamName .. ')' end
    return player .. ' (you)'
end
M.playerLabel = playerLabel

function M.headline(a, steamName)
    return 'Attention: Rule break: ' .. playerLabel(a.player, a.isLocal, steamName) .. '. ' .. RULE_TEXT[a.rule]
end

-- Every rule break, one line each, kept across runs; the same file and line as the DLL's
-- (er_duel_rules_core::feed::Entry::file_line).
M.BREAKS_FILE_NAME = 'er-duel-rules-breaks.txt'

local function stampText(when)
    return string.format('%04d-%02d-%02d %02d:%02d:%02d', when.year, when.month, when.day, when.hour, when.min, when.sec)
end

-- `YYYY-MM-DD HH:MM:SS  <player>  <rule>  --  <detail>`. `when` is os.date('*t').
function M.breakLine(when, a, steamName)
    local line = stampText(when) .. '  ' .. playerLabel(a.player, a.isLocal, steamName) .. '  ' .. RULE_TEXT[a.rule]
    if a.detail and a.detail ~= '' then line = line .. '  --  ' .. a.detail end
    return line
end

-- Breaks shown in the window at once, newest first (feed.rs SHOWN).
M.SHOWN = 10

-- ==========================================================================================
-- Reading the game, through Cheat Engine. Everything below runs inside Cheat Engine only.
-- ==========================================================================================

-- Little-endian decoders over a byte string read in one go.
local function bytesAt(address, count)
    local t = readBytes(address, count, true)
    if t == nil or #t < count then error(string.format('cannot read %d bytes at %X', count, address)) end
    local parts = {}
    for i = 1, #t, 4096 do
        parts[#parts + 1] = string.char(table.unpack(t, i, math.min(i + 4095, #t)))
    end
    return table.concat(parts)
end
local function s32(s, off) return (string.unpack('<i4', s, off + 1)) end
local function u32(s, off) return (string.unpack('<I4', s, off + 1)) end
local function u16(s, off) return (string.unpack('<I2', s, off + 1)) end
local function u8(s, off) return (string.unpack('<I1', s, off + 1)) end
local function i8(s, off) return (string.unpack('<i1', s, off + 1)) end
local function i16(s, off) return (string.unpack('<i2', s, off + 1)) end
local function ptr(s, off) return (string.unpack('<I8', s, off + 1)) end
local function float(s, off) return (string.unpack('<f', s, off + 1)) end

local function rptr(address)
    return ptr(bytesAt(address, 8), 0)
end

-- The table's Auto Assembler half records every SpEffect that took hold, owner and id, in a ring
-- of 256 entries: +0 the count written so far, +0x10 the entries, 16 bytes each (owner pointer,
-- effect id, and the count after that write, stored last so a half-written entry is never taken).
M.RING_SLOTS = 256

-- The entries written since `seen` as {owner = {id, ...}}, and the count to pass next time.
-- `readBlock` returns the 4 KB of entries; it is called only when there is something new. The
-- first call only takes the count, as the Frida agent's hook starts empty.
function M.ringEntries(count, seen, readBlock)
    local out = {}
    if seen == nil or count < seen then return out, count end
    local first = math.max(seen + 1, count - M.RING_SLOTS + 1)
    if count < first then return out, count end
    local block = readBlock()
    for seq = first, count do
        local off = ((seq - 1) % M.RING_SLOTS) * 16
        if u32(block, off + 12) == seq then
            local owner = ptr(block, off)
            out[owner] = out[owner] or {}
            local list = out[owner]
            list[#list + 1] = s32(block, off + 8)
        end
    end
    return out, count
end

-- The ring address an earlier copy of this table left hooked in, or nil when the site is not
-- that hook. Cheat Engine closed without unticking leaves its hook and ring alive in the game;
-- adopting them lets the entry be ticked again instead of needing a game restart. The cave is
-- recognised by its opening instructions, the same bytes the generator assembles:
-- `test eax,eax; js; mov ecx,1; lock xadd [rip+disp32],ecx`.
function M.leftoverRing(siteBytes, siteAddress, readCave)
    if siteBytes:byte(1) ~= 0xE9 then return nil end
    local cave = siteAddress + 5 + string.unpack('<i4', siteBytes, 2)
    local c = readCave(cave, 0x15)
    if c:sub(1, 4) ~= '\x85\xC0\x0F\x88' or c:sub(9, 13) ~= '\xB9\x01\x00\x00\x00' or c:sub(14, 17) ~= '\xF0\x0F\xC1\x0D' then
        return nil
    end
    return cave + 0x15 + string.unpack('<i4', c, 18)
end

function M.runtime()
    local R = {}
    local base = getAddress('eldenring.exe')
    if base == nil or base == 0 then error('eldenring.exe is not open in Cheat Engine') end
    local function at(rva) return base + rva end

    local modulePath = nil
    for _, mod in ipairs(enumModules() or {}) do
        if string.lower(mod.Name) == 'eldenring.exe' then modulePath = mod.PathToFile end
    end
    local gameDir = modulePath and modulePath:match('^(.*[\\/])') or ''
    R.gameDir = gameDir

    -- A file rather than print(): print opens Cheat Engine's Lua window over the game.
    local logPath = gameDir .. 'er-duel-rules-ce.log'
    local function say(line)
        local stamped = '[' .. os.date('%Y-%m-%d %H:%M:%S') .. '] ' .. line
        local f = io.open(logPath, 'a')
        if f then f:write(stamped .. '\n'); f:close() end
    end
    R.say = say

    local header = base + u32(bytesAt(base + 0x3c, 4), 0)
    local stamp = u32(bytesAt(header + 8, 4), 0)
    if stamp ~= M.BUILD.timeDateStamp then
        error(string.format('er-duel-rules: refusing to run -- this table is written for %s (exe timestamp 0x%x) and this game is 0x%x. '
            .. 'Every address it reads belongs to that build.', M.BUILD.label, M.BUILD.timeDateStamp, stamp))
    end

    -- -- params ------------------------------------------------------------------------------
    local paramCache = {}
    local function paramFile(name)
        local def = PARAM_FIELDS[name]
        local cached = paramCache[name]
        if cached ~= nil then return cached or nil end
        local repo = rptr(at(M.RVA.soloParamRepository))
        if repo == 0 then return nil end
        local holder = repo + OFF.sprHolders + def.index * OFF.holderSize
        if u32(bytesAt(holder, 4), 0) < 1 then return nil end
        local resCap = rptr(holder + OFF.holderResCaps)
        if resCap == 0 then return nil end
        local fd4 = rptr(resCap + OFF.paramResCap)
        if fd4 == 0 then return nil end
        local file = rptr(fd4 + OFF.fd4ParamResCapData)
        if file == 0 then return nil end
        local head = bytesAt(file, 0x40)
        local format2d = u8(head, 0x2d)
        local structName
        if format2d & 0x80 ~= 0 then
            structName = readString(file + u32(head, 0x10), 64)
        else
            structName = readString(file + 0xc, 64)
        end
        if structName ~= def.struct then
            say(string.format('er-duel-rules: param slot %d holds %s, expected %s; that table is not read', def.index, tostring(structName), def.struct))
            paramCache[name] = false
            return nil
        end
        if format2d & 0x04 == 0 then
            say(string.format('er-duel-rules: %s does not use 64-bit row offsets; that table is not read', def.struct))
            paramCache[name] = false
            return nil
        end
        local rowCount = u16(head, 0xa)
        local afterName = u32(bytesAt(file - 0x10, 4), 0)
        local lookupTable = file + math.ceil(afterName / 0x10) * 0x10
        local pf = { file = file, rowCount = rowCount, lookupTable = lookupTable, rows = {} }
        paramCache[name] = pf
        return pf
    end
    local function row(name, id)
        local pf = paramFile(name)
        if pf == nil or id < 0 then return nil end
        local hit = pf.rows[id]
        if hit ~= nil then return hit or nil end
        local lo, hi, found = 0, pf.rowCount - 1, nil
        while lo <= hi do
            local mid = (lo + hi) >> 1
            local e = bytesAt(pf.lookupTable + mid * 8, 8)
            local key = u32(e, 0)
            if key == id then found = u32(e, 4); break end
            if key < id then lo = mid + 1 else hi = mid - 1 end
        end
        local result = false
        if found ~= nil then
            local descriptor = bytesAt(pf.file + 0x40 + found * 24, 16)
            if u32(descriptor, 0) == id then result = pf.file + ptr(descriptor, 8) end
        end
        pf.rows[id] = result
        return result or nil
    end
    local function field(name, address, f)
        local d = PARAM_FIELDS[name].fields[f]
        local s = bytesAt(address + d[1], 4)
        local kind = d[2]
        if kind == 'f32' then return float(s, 0) end
        if kind == 'u8' then return u8(s, 0) end
        if kind == 's8' then return i8(s, 0) end
        if kind == 'u16' then return u16(s, 0) end
        if kind == 's16' then return i16(s, 0) end
        if kind == 'u32' then return u32(s, 0) end
        if kind == 'bit' then return (u8(s, 0) >> d[3]) & 1 end
        return s32(s, 0)
    end
    -- Params never change while the game runs, so every answer is kept.
    local memo = { weapon = {}, armorWeight = {}, talismanWeight = {}, talismanEffects = {}, armorEffects = {}, effect = {} }
    local function remember(kind, id, compute)
        local hit = memo[kind][id]
        if hit == nil then
            local value = compute()
            if value == nil then hit = false else hit = value end
            memo[kind][id] = hit
        end
        return hit or nil
    end
    local graph220 = nil
    local facts = {}
    function facts.weapon(id)
        return remember('weapon', id, function()
            local r = row('EquipParamWeapon', weaponRow(id))
            if r == nil then return nil end
            local reinforce = row('ReinforceParamWeapon', field('EquipParamWeapon', r, 'reinforceTypeId') + weaponLevel(id))
            local rate = reinforce == nil and 1 or field('ReinforceParamWeapon', reinforce, 'physicsGuardCutRate')
            local status = false
            for _, f in ipairs({ 'spEffectBehaviorId0', 'spEffectBehaviorId1', 'spEffectBehaviorId2' }) do
                if field('EquipParamWeapon', r, f) > 0 then status = true end
            end
            return {
                wepType = field('EquipParamWeapon', r, 'wepType'),
                weight = field('EquipParamWeapon', r, 'weight'),
                physGuardCut = f32(field('EquipParamWeapon', r, 'physGuardCutRate') * rate),
                inflictsStatus = status,
                takesGrease = field('EquipParamWeapon', r, 'isEnhance') == 1,
            }
        end)
    end
    function facts.armorWeight(id)
        return remember('armorWeight', id, function()
            local r = row('EquipParamProtector', id)
            return r and field('EquipParamProtector', r, 'weight') or nil
        end)
    end
    function facts.talismanWeight(id)
        return remember('talismanWeight', id, function()
            local r = row('EquipParamAccessory', id)
            return r and field('EquipParamAccessory', r, 'weight') or nil
        end)
    end
    function facts.residentEffects(kind, id)
        if kind == 'talisman' then
            return remember('talismanEffects', id, function()
                local r = row('EquipParamAccessory', id)
                if r == nil then return {} end
                local ids = {}
                for _, f in ipairs({ 'residentSpEffectId1', 'residentSpEffectId2', 'residentSpEffectId3', 'residentSpEffectId4' }) do
                    ids[#ids + 1] = field('EquipParamAccessory', r, f)
                end
                if field('EquipParamAccessory', r, 'refCategory') == 2 then ids[#ids + 1] = field('EquipParamAccessory', r, 'refId') end
                return ids
            end)
        end
        return remember('armorEffects', id, function()
            local r = row('EquipParamProtector', id)
            if r == nil then return {} end
            local ids = {}
            for _, f in ipairs({ 'residentSpEffectId', 'residentSpEffectId2', 'residentSpEffectId3' }) do
                ids[#ids + 1] = field('EquipParamProtector', r, f)
            end
            return ids
        end)
    end
    function facts.effect(id)
        return remember('effect', id, function()
            local r = row('SpEffectParam', id)
            if r == nil then return nil end
            return {
                category = field('SpEffectParam', r, 'spCategory'),
                equipWeightRate = field('SpEffectParam', r, 'equipWeightChangeRate'),
                addEndurance = field('SpEffectParam', r, 'addEndureStatus'),
            }
        end)
    end
    function facts.equipLoadBase(endurance)
        if graph220 == nil then
            local r = row('CalcCorrectGraph', 220)
            if r == nil then return nil end
            local function read(prefix)
                local out = {}
                for i = 0, 4 do out[#out + 1] = field('CalcCorrectGraph', r, prefix .. i) end
                return out
            end
            graph220 = { stageMaxVal = read('stageMaxVal'), stageMaxGrowVal = read('stageMaxGrowVal'), adjPtMaxGrowVal = read('adjPt_maxGrowVal') }
        end
        return correctGraphAt(graph220, endurance)
    end
    R.facts = facts

    -- -- the apply ring ----------------------------------------------------------------------
    local ringSeen = nil
    local function drainRing()
        local ring = getAddressSafe('duel_apply_ring')
        if ring == nil or ring == 0 then return {} end
        local count = u32(bytesAt(ring, 4), 0)
        local applied
        applied, ringSeen = M.ringEntries(count, ringSeen, function() return bytesAt(ring + 0x10, M.RING_SLOTS * 16) end)
        return applied
    end

    -- -- players -----------------------------------------------------------------------------
    local queues = {}
    R.pending = 0
    -- Ring entries for a judged player, and those whose effect had already left the list: the
    -- one-frame effects only the hook can show.
    R.ringApplied, R.ringOnly = 0, 0

    -- Better Multiplayer can be injected after the table starts, so it is looked for again every
    -- few seconds.
    local BETTER_MULTIPLAYER_MODULE = 'bettermultiplayer.dll'
    local bmp = { at = -math.huge, loaded = false }
    local lastSettings = ''
    local function localSettings(gdm)
        local now = getTickCount()
        if now - bmp.at > 5000 then
            local loaded = false
            for _, mod in ipairs(enumModules() or {}) do
                if string.lower(mod.Name) == BETTER_MULTIPLAYER_MODULE then loaded = true end
            end
            bmp = { at = now, loaded = loaded }
        end
        local settings = rptr(gdm + OFF.gdmGameSettings)
        local flipper = rptr(at(M.RVA.flipper))
        local spf = flipper == 0 and 0 or float(bytesAt(flipper + OFF.flipperTargetSpf, 4), 0)
        local s = {
            manualAttackAiming = settings ~= 0 and u8(bytesAt(settings + OFF.settingsManualAttackAiming, 1), 0) ~= 0,
            betterMultiplayer = bmp.loaded,
            targetFps = spf > 0 and 1 / spf or 0,
            measuredFps = flipper == 0 and 0 or float(bytesAt(flipper + OFF.flipperMeasuredFps, 4), 0),
        }
        local line = string.format('manual_attack_aiming=%s better_multiplayer=%s target_fps=%.1f',
            tostring(s.manualAttackAiming), tostring(s.betterMultiplayer), s.targetFps)
        if line ~= lastSettings then
            say(string.format('settings: %s measured_fps=%.1f', line, s.measuredFps))
            lastSettings = line
        end
        return s
    end

    function R.readPlayers()
        local wcm = rptr(at(M.RVA.worldChrMan))
        local gdm = rptr(at(M.RVA.gameDataMan))
        local applied = drainRing()
        if wcm == 0 or gdm == 0 then queues = {}; return {} end
        local main = rptr(wcm + OFF.wcmMainPlayer)
        local set = wcm + OFF.wcmPlayerChrSet
        local setHead = bytesAt(set, 0x20)
        local capacity = math.min(u32(setHead, OFF.chrSetCapacity), 64)
        local entries = ptr(setHead, OFF.chrSetEntries)
        local gdmHead = bytesAt(gdm, 0x28)
        local remoteList = ptr(gdmHead, OFF.gdmRemoteList)
        local remoteStates = ptr(gdmHead, OFF.gdmRemoteStates)

        local bullets = {}
        local bm = rptr(at(M.RVA.bulletManager))
        if bm ~= 0 then
            local b = rptr(bm + OFF.bmHead)
            local n = 0
            while b ~= 0 and n < WALK_LIMIT do
                local s = bytesAt(b + OFF.bulletOwner, OFF.bulletNext + 8 - OFF.bulletOwner)
                local owner = ptr(s, 0)
                local id = nil
                for _, o in ipairs(OFF.bulletParamIds) do
                    local x = s32(s, o - OFF.bulletOwner)
                    if x > 0 then id = x; break end
                end
                if id ~= nil then
                    bullets[owner] = bullets[owner] or {}
                    local list = bullets[owner]
                    list[#list + 1] = id
                end
                b = ptr(s, OFF.bulletNext - OFF.bulletOwner)
                n = n + 1
            end
        end

        local players = {}
        local seen = {}
        R.pending = 0
        local slots = capacity > 0 and bytesAt(entries, capacity * OFF.chrSetEntryStride) or ''
        for i = 0, capacity - 1 do
            local chr = ptr(slots, i * OFF.chrSetEntryStride)
            if chr ~= 0 then
                local ok, err = pcall(function()
                    local key = rptr(chr + OFF.chrHandle)
                    local isLocal = chr == main
                    local pgd = rptr(chr + OFF.playerGameData)
                    if not isLocal then
                        local delta = pgd - remoteList
                        local index = delta / OFF.pgdSize
                        if delta % OFF.pgdSize ~= 0 or index < 0 or index >= REMOTE_SLOTS
                            or u8(bytesAt(remoteStates + math.floor(index), 1), 0) ~= REMOTE_FULLY_SYNCED then
                            R.pending = R.pending + 1
                            return
                        end
                    end
                    seen[key] = true
                    local g = bytesAt(pgd, OFF.pgdPhysick + 12)
                    local asm = OFF.pgdChrAsm
                    local function ids(k) return s32(g, asm + OFF.asmParamIds + k * 4) end
                    local function tear(k)
                        local raw = u32(g, OFF.pgdPhysick + k * 4)
                        if raw == 0xffffffff then return NO_ITEM end
                        return raw & 0x0fffffff
                    end
                    local modules = rptr(chr + OFF.chrModules)
                    local timeAct = rptr(modules + OFF.modTimeAct)
                    local ta = bytesAt(timeAct, OFF.timeActWriteIdx + 4)
                    local history = {}
                    for q = 0, OFF.timeActCount - 1 do
                        local o = OFF.timeActQueue + q * OFF.timeActEntry
                        history[#history + 1] = { s32(ta, o), float(ta, o + OFF.timeActPlayTime) }
                    end
                    local newAnims, cursor = animStarts(queues[key], history, u32(ta, OFF.timeActWriteIdx))
                    if cursor then queues[key] = cursor end
                    local speffects, have = {}, {}
                    local entry = rptr(rptr(chr + OFF.chrSpecialEffect) + OFF.speHead)
                    local n = 0
                    while entry ~= 0 and n < WALK_LIMIT do
                        local e = bytesAt(entry, OFF.speEntryNext + 8)
                        local id = s32(e, OFF.speEntryParamId)
                        speffects[#speffects + 1] = id
                        have[id] = true
                        entry = ptr(e, OFF.speEntryNext)
                        n = n + 1
                    end
                    -- Effects applied since the last read that have already ended (a heal, a flask).
                    for _, id in ipairs(applied[chr] or {}) do
                        R.ringApplied = R.ringApplied + 1
                        if not have[id] then
                            speffects[#speffects + 1] = id
                            have[id] = true
                            R.ringOnly = R.ringOnly + 1
                        end
                    end
                    local chars = {}
                    for c = 0, OFF.pgdNameChars - 1 do
                        local ch = u16(g, OFF.pgdName + c * 2)
                        if ch == 0 then break end
                        chars[#chars + 1] = utf8.char(ch)
                    end
                    local rideModule = rptr(modules + OFF.modRide)
                    local rideNode = rptr(rideModule + OFF.rideNode)
                    local attributes = {}
                    for k = 0, 7 do attributes[#attributes + 1] = u32(g, OFF.pgdAttributes + k * 4) end
                    players[#players + 1] = {
                        key = string.format('0x%x', key),
                        address = chr,
                        name = table.concat(chars),
                        isLocal = isLocal,
                        level = u32(g, OFF.pgdLevel),
                        endurance = u32(g, OFF.pgdEndurance),
                        attributes = attributes,
                        armStyle = u32(g, asm + OFF.asmArmStyle),
                        leftSlot = u32(g, asm + OFF.asmLeftSlot),
                        rightSlot = u32(g, asm + OFF.asmRightSlot),
                        weaponsLeft = { ids(0), ids(2), ids(4) },
                        weaponsRight = { ids(1), ids(3), ids(5) },
                        armor = { ids(12), ids(13), ids(14), ids(15) },
                        talismans = { ids(17), ids(18), ids(19), ids(20) },
                        physick = { tear(0), tear(1), tear(2) },
                        speffects = speffects,
                        newAnims = newAnims,
                        bullets = bullets[key] or {},
                        hp = s32(bytesAt(rptr(modules + OFF.modData) + OFF.dataHp, 4), 0),
                        riding = rideNode ~= 0 and u32(bytesAt(rideNode + OFF.rideState, 4), 0) ~= 0,
                        localSettings = isLocal and localSettings(gdm) or nil,
                        runeArcFlag = u8(g, OFF.pgdRuneArcActive) ~= 0,
                    }
                end)
                if not ok then R.readErrors = (R.readErrors or 0) + 1; R.lastReadError = tostring(err) end
            end
        end
        for k in pairs(queues) do if not seen[k] then queues[k] = nil end end
        return players
    end

    -- The local Steam name, asked of the steam_api64.dll the game loads; asked until Steam first
    -- answers, then kept.
    local steamName = nil
    local steamTries = 0
    function R.steamName()
        if steamName ~= nil or steamTries >= 5 then return steamName end
        steamTries = steamTries + 1
        local ok, name = pcall(function()
            local friendsFn = getAddressSafe('steam_api64.SteamAPI_SteamFriends_v017')
            local personaFn = getAddressSafe('steam_api64.SteamAPI_ISteamFriends_GetPersonaName')
            if friendsFn == nil or personaFn == nil then return nil end
            local friends = executeCodeEx(0, 2000, friendsFn)
            if friends == nil or friends == 0 then return nil end
            local text = executeCodeEx(0, 2000, personaFn, friends)
            if text == nil or text == 0 then return nil end
            return readString(text, 128)
        end)
        if ok and name and name ~= '' then
            steamName = name
            say(string.format('steam: the local player is listed as "%s"', name))
        elseif not ok then
            say('steam: no Steam name (' .. tostring(name) .. '); the local player is listed as (you)')
        end
        return steamName
    end

    return R
end

-- Refuse before anything is hooked: a different game build, or a hook site that holds neither the
-- code the table's Auto Assembler half replaces (the bytes as hex, space-separated) nor this
-- table's own hook left behind. Returns the leftover hook's ring address, or nil for a clean site.
function M.preflight(hookRva, expected)
    local base = getAddress('eldenring.exe')
    if base == nil or base == 0 then error('er-duel-rules: eldenring.exe is not open in Cheat Engine') end
    local header = base + u32(bytesAt(base + 0x3c, 4), 0)
    local stamp = u32(bytesAt(header + 8, 4), 0)
    if stamp ~= M.BUILD.timeDateStamp then
        error(string.format('er-duel-rules: this table is written for %s (exe timestamp 0x%x) and this game is 0x%x; nothing was hooked',
            M.BUILD.label, M.BUILD.timeDateStamp, stamp))
    end
    local want = {}
    for hex in expected:gmatch('%x%x') do want[#want + 1] = tonumber(hex, 16) end
    local got = bytesAt(base + hookRva, #want)
    for i, b in ipairs(want) do
        local have = got:byte(i)
        if have ~= b then
            local ring = M.leftoverRing(got, base + hookRva, bytesAt)
            if ring ~= nil then return ring end
            error(string.format('er-duel-rules: the SpEffect apply hook site holds %02X at byte %d where %02X was expected -- '
                .. 'another tool has patched it; nothing was hooked', have, i, b))
        end
    end
    return nil
end

-- -- the window -------------------------------------------------------------------------------
local function makeWindow(breaksPath)
    local form = createForm(false)
    form.Caption = 'Duel rule breaks'
    form.Width = 760
    form.Height = 300
    form.FormStyle = 'fsStayOnTop'
    form.Position = 'poScreenCenter'
    local status = createLabel(form)
    status.Align = 'alBottom'
    status.Caption = 'Watching eldenring.exe. Every break also goes to ' .. breaksPath
    local list = createListView(form)
    list.Align = 'alClient'
    list.ViewStyle = 'vsReport'
    list.ReadOnly = true
    list.RowSelect = true
    list.GridLines = true
    for _, c in ipairs({ { 'Time', 70 }, { 'Player', 200 }, { 'Rule', 280 }, { 'What broke it', 200 } }) do
        local col = list.Columns.add()
        col.Caption = c[1]
        col.Width = c[2]
    end
    form.OnClose = function() return caHide end
    return { form = form, list = list, status = status }
end

local function showRows(window, rows)
    local items = window.list.Items
    items.clear()
    for _, r in ipairs(rows) do
        local item = items.add()
        item.Caption = r[1]
        item.SubItems.add(r[2])
        item.SubItems.add(r[3])
        item.SubItems.add(r[4])
    end
    window.form.show()
end

-- Start the referee on the open process; returns the running state, which M.stop takes.
function M.start()
    local R = M.runtime()
    local referee = Referee.new()
    local breaksPath = R.gameDir .. M.BREAKS_FILE_NAME
    local window = makeWindow(breaksPath)
    window.form.show()
    local rows = {}
    local loggedBuilds = {}
    local started = getTickCount()
    local state = { ticks = 0, alerts = 0, errors = 0 }

    local function recordBreak(line)
        local f = io.open(breaksPath, 'a')
        if f then f:write(line .. '\n'); f:close()
        else R.say('er-duel-rules: cannot append to ' .. breaksPath) end
    end

    local function tick()
        state.ticks = state.ticks + 1
        local ok, players = pcall(R.readPlayers)
        if not ok then
            state.errors = state.errors + 1
            if state.errors == 1 then R.say('er-duel-rules: read failed: ' .. tostring(players)) end
            return
        end
        local steam = nil
        for _, p in ipairs(players) do
            if p.isLocal then steam = R.steamName() end
        end
        for _, p in ipairs(players) do
            local function list(t) local s = {} for i, x in ipairs(t) do s[i] = string.format('%d', x) end return table.concat(s, ', ') end
            local build = string.format('level=%d attributes=[%s] rune_arc=%s arm=%d slots=L%d/R%d right=[%s] left=[%s] armor=[%s] talismans=[%s] physick=[%s]',
                p.level, list(p.attributes), tostring(runeArcActive(p)), p.armStyle, p.leftSlot, p.rightSlot,
                list(p.weaponsRight), list(p.weaponsLeft), list(p.armor), list(p.talismans), list(p.physick))
            if loggedBuilds[p.key] ~= build then
                R.say('player: ' .. playerLabel(p.name, p.isLocal, steam) .. ' ' .. build)
                loggedBuilds[p.key] = build
            end
            for _, anim in ipairs(p.newAnims) do
                local sc = skillClip(anim)
                if sc ~= nil then
                    R.say(string.format('skill: %s anim=%d swordArtsTypeNew=%d clip=%06d', p.name, anim, sc[1], sc[2]))
                end
            end
        end
        local before = {}
        for _, p in ipairs(players) do before[p.key] = referee:flasksUsed(p.key) end
        local fresh = referee:observe(getTickCount() - started, players, R.facts)
        for _, a in ipairs(fresh) do
            state.alerts = state.alerts + 1
            R.say(M.headline(a, steam) .. ' -- ' .. a.detail)
            local when = os.date('*t')
            recordBreak(M.breakLine(when, a, steam))
            table.insert(rows, 1, { string.format('%02d:%02d:%02d', when.hour, when.min, when.sec),
                playerLabel(a.player, a.isLocal, steam), RULE_TEXT[a.rule], a.detail or '' })
            while #rows > M.SHOWN do table.remove(rows) end
        end
        if #fresh > 0 then showRows(window, rows) end
        for _, p in ipairs(players) do
            local n = referee:flasksUsed(p.key)
            if n > before[p.key] then R.say(string.format('flask: %s blue flask %d this round', p.name, n)) end
        end
        if state.ticks % 600 == 0 then
            R.say(string.format('status: ticks=%d players=%d remote_pending=%d alerts=%d read_errors=%d player_read_errors=%d ring_applied=%d ring_only=%d',
                state.ticks, #players, R.pending, state.alerts, state.errors, R.readErrors or 0, R.ringApplied, R.ringOnly))
            if R.lastReadError then R.say('last player read error: ' .. R.lastReadError); R.lastReadError = nil end
        end
    end

    local timer = createTimer(nil, false)
    timer.Interval = 16
    timer.OnTimer = function()
        local ok, err = pcall(tick)
        if not ok then
            state.errors = state.errors + 1
            if state.errors <= 3 then R.say('er-duel-rules: tick failed: ' .. tostring(err)) end
        end
    end
    timer.Enabled = true
    R.say(string.format('er-duel-rules: armed on %s; breaks to %s', M.BUILD.label, breaksPath))
    return { timer = timer, window = window, R = R, state = state }
end

function M.stop(running)
    if running == nil then return end
    running.timer.Enabled = false
    running.timer.destroy()
    running.window.form.destroy()
    running.R.say(string.format('er-duel-rules: stopped after %d ticks, %d breaks', running.state.ticks, running.state.alerts))
end

return M
