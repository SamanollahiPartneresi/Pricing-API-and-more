CREATE OR REPLACE TABLE "pricing_factors" AS
SELECT 1 AS "factor_id", 'Travel Difficulty' AS "category", '1' AS "level", '< 60 minute drive' AS "description", '0' AS "value", FALSE AS "default_flag", 1 AS "order_form_service_id"
UNION ALL
SELECT 2, 'Travel Difficulty', '2', '1-3 hour drive', '7', FALSE, 1
UNION ALL
SELECT 3, 'Travel Difficulty', '3', '3-5 hour drive', '15', FALSE, 1
UNION ALL
SELECT 4, 'Travel Difficulty', '4', '5+ hour drive', '20', FALSE, 1
UNION ALL
SELECT 5, 'Travel Difficulty', '5', 'Easy flight', '25', FALSE, 1
UNION ALL
SELECT 6, 'Travel Difficulty', '6', 'Hard flight', 'RFP', FALSE, 1
UNION ALL
SELECT 7, 'Travel Difficulty', '7', 'Super remote', 'RFP', FALSE, 1
UNION ALL
SELECT 8, 'Prior Report', '1', 'None', '0.00', FALSE, 1
UNION ALL
SELECT 9, 'Prior Report', '2', 'External < 2 years', '-2.50', FALSE, 1
UNION ALL
SELECT 10, 'Prior Report', '3', 'Internal < 10 years', '-5.00', FALSE, 1
UNION ALL
SELECT 11, 'Prior Report', '4', 'Internal < 2 years', '-10.00', FALSE, 1
UNION ALL
SELECT 12, 'Prior Report', '5', 'Internal < 6 months', '-15.00', FALSE, 1
UNION ALL
SELECT 13, 'Site Complexity', '1', 'Simple', '-5', FALSE, 1
UNION ALL
SELECT 14, 'Site Complexity', '2', 'Average', '0', FALSE, 1
UNION ALL
SELECT 15, 'Site Complexity', '3', 'Complicated', '20', FALSE, 1
UNION ALL
SELECT 16, 'Site Complexity', '4', 'Difficult', 'RFP', FALSE, 1
UNION ALL
SELECT 17, 'International', '1', 'US', '0', FALSE, 1
UNION ALL
SELECT 18, 'International', '2', 'CA', 'RFP', FALSE, 1
UNION ALL
SELECT 19, 'Portfolio Size', '1', '1 to 3', '0', FALSE, 1
UNION ALL
SELECT 20, 'Portfolio Size', '2', '4 to 9', '-2', FALSE, 1
UNION ALL
SELECT 21, 'Portfolio Size', '3', '10 to 39', '-5', FALSE, 1
UNION ALL
SELECT 22, 'Portfolio Size', '4', '40 to 69', '-7', FALSE, 1
UNION ALL
SELECT 23, 'Portfolio Size', '5', '70 to 99', '-9', FALSE, 1
UNION ALL
SELECT 24, 'Portfolio Size', '6', '100+', '-12', FALSE, 1
UNION ALL
SELECT 25, 'Limit of Liability', '1', 'Less than or equal to 249,999', '0', FALSE, 1
UNION ALL
SELECT 26, 'Limit of Liability', '2', 'Between 250,000 and 999,999; inclusive of 250,000', '2.5', FALSE, 1
UNION ALL
SELECT 27, 'Limit of Liability', '3', 'Between 1,000,000 and 4,999,999; inclusive of 1,000,000', '5', FALSE, 1
UNION ALL
SELECT 28, 'Limit of Liability', '4', '5,000,000 or greater; inclusive of 5,000,000', '7.5', FALSE, 1
UNION ALL
SELECT 29, 'Turnaround Time', '1', '1 day', 'RFP', FALSE, 1
UNION ALL
SELECT 30, 'Turnaround Time', '2', '2 days', 'RFP', FALSE, 1
UNION ALL
SELECT 31, 'Turnaround Time', '3', '3 days', 'RFP', FALSE, 1
UNION ALL
SELECT 32, 'Turnaround Time', '4', '4 days', 'RFP', FALSE, 1
UNION ALL
SELECT 33, 'Turnaround Time', '5', '5 days', 'RFP', FALSE, 1
UNION ALL
SELECT 34, 'Turnaround Time', '6', '6 days', 'RFP', FALSE, 1
UNION ALL
SELECT 35, 'Turnaround Time', '7', '7 days', 'RFP', FALSE, 1
UNION ALL
SELECT 36, 'Turnaround Time', '8', '8 days', 'RFP', FALSE, 1
UNION ALL
SELECT 37, 'Turnaround Time', '9', '9 days', 'RFP', FALSE, 1
UNION ALL
SELECT 38, 'Turnaround Time', '10', '10 days', '20', FALSE, 1
UNION ALL
SELECT 39, 'Turnaround Time', '11', '11 days', '10', FALSE, 1
UNION ALL
SELECT 40, 'Turnaround Time', '12', '12 days', '10', FALSE, 1
UNION ALL
SELECT 41, 'Turnaround Time', '13', '13 days', '0', FALSE, 1
UNION ALL
SELECT 42, 'Turnaround Time', '14', '14 days', '0', FALSE, 1
UNION ALL
SELECT 43, 'Turnaround Time', '15', '15 days', '0', FALSE, 1
UNION ALL
SELECT 44, 'Turnaround Time', '16', '16 days', '0', FALSE, 1
UNION ALL
SELECT 45, 'Turnaround Time', '17', '17 days', '-5', FALSE, 1
UNION ALL
SELECT 46, 'Turnaround Time', '18', '18 days', '-5', FALSE, 1
UNION ALL
SELECT 47, 'Turnaround Time', '19', '19 days', '-5', FALSE, 1
UNION ALL
SELECT 48, 'Turnaround Time', '20', '20 days', '-5', FALSE, 1
UNION ALL
SELECT 49, 'Size', '1', 'XS: Applied to certain Primary Property Types', '-10', FALSE, 1
UNION ALL
SELECT 50, 'Size', '2', 'S: Building SF <= 50,000', '0', FALSE, 1
UNION ALL
SELECT 51, 'Size', '3', 'M: Building SF <= 100,000', '5', FALSE, 1
UNION ALL
SELECT 52, 'Size', '4', 'L:Building SF <= 250,000', '7', FALSE, 1
UNION ALL
SELECT 53, 'Size', '5', 'XL:Building SF <= 500,000', 'RFP', FALSE, 1
UNION ALL
SELECT 54, 'Size', '6', '2XL:Building SF <= 1,000,000', 'RFP', FALSE, 1
UNION ALL
SELECT 55, 'Size', '7', '3XL: Building SF > 1,000,000', 'RFP', FALSE, 1
UNION ALL
SELECT 56, 'Unit Inspection', '1', '1 to 20 units', '0', FALSE, 1
UNION ALL
SELECT 57, 'Unit Inspection', '2', '21 to 40 units', '15', FALSE, 1
UNION ALL
SELECT 58, 'Unit Inspection', '3', '41 to 60 units', '25', FALSE, 1
UNION ALL
SELECT 59, 'Unit Inspection', '4', '61 to 80 units', 'RFP', FALSE, 1
UNION ALL
SELECT 60, 'Unit Inspection', '5', '81 to 100 units', 'RFP', FALSE, 1
UNION ALL
SELECT 61, '# of Buildings 1', '1', '1 to 8 bldgs', '0', FALSE, 1
UNION ALL
SELECT 62, '# of Buildings 1', '2', '9 to 14 bldgs', '10', FALSE, 1
UNION ALL
SELECT 63, '# of Buildings 1', '3', '15 to 20 bldgs', 'RFP', FALSE, 1
UNION ALL
SELECT 64, '# of Buildings 1', '4', '20+ bldgs', 'RFP', FALSE, 1
UNION ALL
SELECT 65, '# of Buildings 2', '1', '1', '0', FALSE, 1
UNION ALL
SELECT 66, '# of Buildings 2', '2', '2 bldgs', '5', FALSE, 1
UNION ALL
SELECT 67, '# of Buildings 2', '3', '3 to 5 bldgs', '10', FALSE, 1
UNION ALL
SELECT 68, '# of Buildings 2', '4', '6 to 9 bldgs', 'RFP', FALSE, 1
UNION ALL
SELECT 69, '# of Buildings 2', '5', '10+ bldgs', 'RFP', FALSE, 1
UNION ALL
SELECT 70, '# of Stories', '1', '0 to 10 stories', '0', FALSE, 1
UNION ALL
SELECT 71, '# of Stories', '2', '11 to 15 stories', 'RFP', FALSE, 1
UNION ALL
SELECT 72, '# of Stories', '3', '16+ stories', 'RFP', FALSE, 1
UNION ALL
SELECT 73, 'Time Period', '1', 'Adjust depending on Partner''s busy level', '35', FALSE, 1
UNION ALL
SELECT 74, 'Unit Inspection', '1', '1 to 20 units', '0', FALSE, 2
UNION ALL
SELECT 75, 'Unit Inspection', '2', '21 to 40 units', '10', FALSE, 2
UNION ALL
SELECT 76, 'Unit Inspection', '3', '41 to 60 units', '20', FALSE, 2
UNION ALL
SELECT 77, 'Unit Inspection', '4', '61 to 80 units', 'RFP', FALSE, 2
UNION ALL
SELECT 78, 'Unit Inspection', '5', '81 to 100 units', 'RFP', FALSE, 2
UNION ALL
SELECT 79, 'Travel Difficulty', '1', '< 60 minute drive', '0', FALSE, 2
UNION ALL
SELECT 80, 'Travel Difficulty', '2', '1-3 hour drive', '5', FALSE, 2
UNION ALL
SELECT 81, 'Travel Difficulty', '3', '3-5 hour drive', '10', FALSE, 2
UNION ALL
SELECT 82, 'Travel Difficulty', '4', '5+ hour drive', '20', FALSE, 2
UNION ALL
SELECT 83, 'Travel Difficulty', '5', 'Easy flight', '25', FALSE, 2
UNION ALL
SELECT 84, 'Travel Difficulty', '6', 'Hard flight', 'RFP', FALSE, 2
UNION ALL
SELECT 85, 'Travel Difficulty', '7', 'Super remote', 'RFP', FALSE, 2
UNION ALL
SELECT 86, 'Prior Report', '1', 'None', '0.00', FALSE, 2
UNION ALL
SELECT 87, 'Prior Report', '2', 'External < 2 years', '-2.50', FALSE, 2
UNION ALL
SELECT 88, 'Prior Report', '3', 'Internal < 10 years', '-5.00', FALSE, 2
UNION ALL
SELECT 89, 'Prior Report', '4', 'Internal < 2 years', '-10.00', FALSE, 2
UNION ALL
SELECT 90, 'Prior Report', '5', 'Internal < 6 months', '-15.00', FALSE, 2
UNION ALL
SELECT 91, 'Site Complexity', '1', 'Simple', '-5', FALSE, 2
UNION ALL
SELECT 92, 'Site Complexity', '2', 'Average', '0', FALSE, 2
UNION ALL
SELECT 93, 'Site Complexity', '3', 'Complicated', '10', FALSE, 2
UNION ALL
SELECT 94, 'Site Complexity', '4', 'Difficult', 'RFP', FALSE, 2
UNION ALL
SELECT 95, 'International', '1', 'US', '0', FALSE, 2
UNION ALL
SELECT 96, 'International', '2', 'CA', 'RFP', FALSE, 2
UNION ALL
SELECT 97, 'Portfolio Size', '1', '1 to 3', '0', FALSE, 2
UNION ALL
SELECT 98, 'Portfolio Size', '2', '4 to 9', '-2', FALSE, 2
UNION ALL
SELECT 99, 'Portfolio Size', '3', '10 to 39', '-5', FALSE, 2
UNION ALL
SELECT 100, 'Portfolio Size', '4', '40 to 69', '-7', FALSE, 2
UNION ALL
SELECT 101, 'Portfolio Size', '5', '70 to 99', '-9', FALSE, 2
UNION ALL
SELECT 102, 'Portfolio Size', '6', '100+', '-12', FALSE, 2
UNION ALL
SELECT 103, 'Limit of Liability', '1', 'Less than or equal to 249,999', '0', FALSE, 2
UNION ALL
SELECT 104, 'Limit of Liability', '2', 'Between 250,000 and 999,999; inclusive of 250,000', '2.5', FALSE, 2
UNION ALL
SELECT 105, 'Limit of Liability', '3', 'Between 1,000,000 and 4,999,999; inclusive of 1,000,000', '5', FALSE, 2
UNION ALL
SELECT 106, 'Limit of Liability', '4', '5,000,000 or greater; inclusive of 5,000,000', '7.5', FALSE, 2
UNION ALL
SELECT 107, 'Turnaround Time', '1', '1 day', 'RFP', FALSE, 2
UNION ALL
SELECT 108, 'Turnaround Time', '2', '2 days', 'RFP', FALSE, 2
UNION ALL
SELECT 109, 'Turnaround Time', '3', '3 days', 'RFP', FALSE, 2
UNION ALL
SELECT 110, 'Turnaround Time', '4', '4 days', 'RFP', FALSE, 2
UNION ALL
SELECT 111, 'Turnaround Time', '5', '5 days', 'RFP', FALSE, 2
UNION ALL
SELECT 112, 'Turnaround Time', '6', '6 days', 'RFP', FALSE, 2
UNION ALL
SELECT 113, 'Turnaround Time', '7', '7 days', 'RFP', FALSE, 2
UNION ALL
SELECT 114, 'Turnaround Time', '8', '8 days', 'RFP', FALSE, 2
UNION ALL
SELECT 115, 'Turnaround Time', '9', '9 days', 'RFP', FALSE, 2
UNION ALL
SELECT 116, 'Turnaround Time', '10', '10 days', '20', FALSE, 2
UNION ALL
SELECT 117, 'Turnaround Time', '11', '11 days', '10', FALSE, 2
UNION ALL
SELECT 118, 'Turnaround Time', '12', '12 days', '10', FALSE, 2
UNION ALL
SELECT 119, 'Turnaround Time', '13', '13 days', '0', FALSE, 2
UNION ALL
SELECT 120, 'Turnaround Time', '14', '14 days', '0', FALSE, 2
UNION ALL
SELECT 121, 'Turnaround Time', '15', '15 days', '0', FALSE, 2
UNION ALL
SELECT 122, 'Turnaround Time', '16', '16 days', '0', FALSE, 2
UNION ALL
SELECT 123, 'Turnaround Time', '17', '17 days', '-5', FALSE, 2
UNION ALL
SELECT 124, 'Turnaround Time', '18', '18 days', '-5', FALSE, 2
UNION ALL
SELECT 125, 'Turnaround Time', '19', '19 days', '-5', FALSE, 2
UNION ALL
SELECT 126, 'Turnaround Time', '20', '20 days', '-5', FALSE, 2
UNION ALL
SELECT 127, 'Size', '1', 'XS: Applied to certain Primary Property Types', '-10', FALSE, 2
UNION ALL
SELECT 128, 'Size', '2', 'S: Land Ac <= 4', '0', FALSE, 2
UNION ALL
SELECT 129, 'Size', '3', 'M: Land Ac <= 8', '5', FALSE, 2
UNION ALL
SELECT 130, 'Size', '4', 'L: Land Ac <= 12', '7', FALSE, 2
UNION ALL
SELECT 131, 'Size', '5', 'XL: Land Ac <= 20', 'RFP', FALSE, 2
UNION ALL
SELECT 132, 'Size', '6', '2XL: Land Ac <= 50', 'RFP', FALSE, 2
UNION ALL
SELECT 133, 'Size', '7', '3XL: Land Ac > 50', 'RFP', FALSE, 2
UNION ALL
SELECT 134, 'Time Period', '1', 'Adjust depending on Partner''s busy level', '10', FALSE, 2
UNION ALL
SELECT 135, '# of Stories', '1', '0 to 10 stories', '0', FALSE, 2
UNION ALL
SELECT 136, '# of Stories', '2', '11 to 15 stories', '4', FALSE, 2
UNION ALL
SELECT 137, '# of Stories', '3', '16+ stories', 'RFP', FALSE, 2
UNION ALL
SELECT 138, '# of Buildings 1', '1', '1 to 5 bldgs', '0', FALSE, 2
UNION ALL
SELECT 139, '# of Buildings 1', '2', '6 to 18 bldgs', '7', FALSE, 2
UNION ALL
SELECT 140, '# of Buildings 1', '3', '19 to 35 bldgs', '15', FALSE, 2
UNION ALL
SELECT 141, '# of Buildings 1', '4', '36+ bldgs', 'RFP', FALSE, 2
UNION ALL
SELECT 142, '# of Buildings 2', '1', '1', '0', FALSE, 2
UNION ALL
SELECT 143, '# of Buildings 2', '2', '2 bldgs', '2', FALSE, 2
UNION ALL
SELECT 144, '# of Buildings 2', '3', '3 to 5 bldgs', '4', FALSE, 2
UNION ALL
SELECT 145, '# of Buildings 2', '4', '6 to 9 bldgs', '6', FALSE, 2
UNION ALL
SELECT 146, '# of Buildings 2', '5', '10+ bldgs', 'RFP', FALSE, 2
UNION ALL
SELECT 147, 'Turnaround Time', '1', 'Less than or equal to 5 days', '25', FALSE, 3
UNION ALL
SELECT 148, 'Turnaround Time', '2', 'Less than or equal to 10 days', '15', FALSE, 3
UNION ALL
SELECT 149, 'Turnaround Time', '3', 'Less than or equal to 15 days', '8', FALSE, 3
UNION ALL
SELECT 150, 'Turnaround Time', '4', 'Greater than or equal to 20 days', '0', FALSE, 3
UNION ALL
SELECT 151, 'Size', 'XS', 'Building SF <= 10000', '0', FALSE, 3
UNION ALL
SELECT 152, 'Size', 'S', 'Building SF <= 30000', '0', FALSE, 3
UNION ALL
SELECT 153, 'Size', 'M', 'Building SF <= 100000', '2', FALSE, 3
UNION ALL
SELECT 154, 'Size', 'L', 'Building SF <= 250000', '3', FALSE, 3
UNION ALL
SELECT 155, 'Size', 'XL', 'Building SF > 250000', '5', FALSE, 3
UNION ALL
SELECT 156, '# of Stories', '1', '1 to 3', '0', FALSE, 3
UNION ALL
SELECT 157, '# of Stories', '2', '4 to 10', '0', FALSE, 3
UNION ALL
SELECT 158, '# of Stories', '3', '11+', '2', FALSE, 3
UNION ALL
SELECT 159, '# of Buildings', '1', '1 to 2', '0', FALSE, 3
UNION ALL
SELECT 160, '# of Buildings', '2', '3 to 5', '5', FALSE, 3
UNION ALL
SELECT 161, '# of Buildings', '3', '6+', '10', FALSE, 3
UNION ALL
SELECT 162, 'Travel Difficulty', '1', 'Easy', '0', FALSE, 3
UNION ALL
SELECT 163, 'Travel Difficulty', '2', 'Moderate', '10', FALSE, 3
UNION ALL
SELECT 164, 'Travel Difficulty', '3', 'Difficult', '20', FALSE, 3
UNION ALL
SELECT 165, 'Travel Difficulty', '4', 'Remote', '30', FALSE, 3
UNION ALL
SELECT 166, 'Limit of Liability', '1', 'Less than or equal to 250000', '5', FALSE, 3
UNION ALL
SELECT 167, 'Limit of Liability', '2', 'Between 250000 and 1000000', '10', FALSE, 3
UNION ALL
SELECT 168, 'Limit of Liability', '3', 'Between 1000000 and 5000000', '15', FALSE, 3
UNION ALL
SELECT 169, 'Limit of Liability', '4', '5000000 or greater', '25', FALSE, 3
UNION ALL
SELECT 170, 'Portfolio Size', '1', '1 to 5', '0', FALSE, 3
UNION ALL
SELECT 171, 'Portfolio Size', '2', '6 to 10', '5', FALSE, 3
UNION ALL
SELECT 172, 'Portfolio Size', '3', '11 to 20', '10', FALSE, 3
UNION ALL
SELECT 173, 'Portfolio Size', '4', '21+', '15', FALSE, 3
UNION ALL
SELECT 174, 'Site Complexity', '1', 'Simple', '0', FALSE, 3
UNION ALL
SELECT 175, 'Site Complexity', '2', 'Average', '15', FALSE, 3
UNION ALL
SELECT 176, 'Site Complexity', '3', 'Complicated', '30', FALSE, 3
UNION ALL
SELECT 177, 'Site Complexity', '4', 'Difficult', '50', FALSE, 3
UNION ALL
SELECT 178, 'International', '1', 'US', '0', FALSE, 3
UNION ALL
SELECT 179, 'International', '2', 'CA', '20', FALSE, 3
UNION ALL
SELECT 180, 'Prior Report', '1', 'Internal < 6 months', '-20', FALSE, 3
UNION ALL
SELECT 181, 'Prior Report', '2', 'Internal < 2 years', '-10', FALSE, 3
UNION ALL
SELECT 182, 'Prior Report', '3', 'Internal < 10 years', '0', FALSE, 3
UNION ALL
SELECT 183, 'Prior Report', '4', 'External < 2 years', '5', FALSE, 3
UNION ALL
SELECT 184, 'Time Period', '1', 'Busy Level', '5', FALSE, 3
UNION ALL
SELECT 185, 'Travel Difficulty', '1', '< 60 minute drive', '0', FALSE, 4
UNION ALL
SELECT 186, 'Travel Difficulty', '2', '1-3 hour drive', '7', FALSE, 4
UNION ALL
SELECT 187, 'Travel Difficulty', '3', '3-5 hour drive', '15', FALSE, 4
UNION ALL
SELECT 188, 'Travel Difficulty', '4', '5+ hour drive', '20', FALSE, 4
UNION ALL
SELECT 189, 'Travel Difficulty', '5', 'Easy flight', '25', FALSE, 4
UNION ALL
SELECT 190, 'Travel Difficulty', '6', 'Hard flight', 'RFP', FALSE, 4
UNION ALL
SELECT 191, 'Travel Difficulty', '7', 'Super remote', 'RFP', FALSE, 4
UNION ALL
SELECT 192, 'Prior Report', '1', 'None', '0.00', FALSE, 4
UNION ALL
SELECT 193, 'Prior Report', '2', 'External < 2 years', '-2.50', FALSE, 4
UNION ALL
SELECT 194, 'Prior Report', '3', 'Internal < 10 years', '-5.00', FALSE, 4
UNION ALL
SELECT 195, 'Prior Report', '4', 'Internal < 2 years', '-10.00', FALSE, 4
UNION ALL
SELECT 196, 'Prior Report', '5', 'Internal < 6 months', '-15.00', FALSE, 4
UNION ALL
SELECT 197, 'Site Complexity', '1', 'Simple', '-5', FALSE, 4
UNION ALL
SELECT 198, 'Site Complexity', '2', 'Average', '0', FALSE, 4
UNION ALL
SELECT 199, 'Site Complexity', '3', 'Complicated', '20', FALSE, 4
UNION ALL
SELECT 200, 'Site Complexity', '4', 'Difficult', 'RFP', FALSE, 4
UNION ALL
SELECT 201, 'International', '1', 'US', '0', FALSE, 4
UNION ALL
SELECT 202, 'International', '2', 'CA', 'RFP', FALSE, 4
UNION ALL
SELECT 203, 'Portfolio Size', '1', '1 to 3', '0', FALSE, 4
UNION ALL
SELECT 204, 'Portfolio Size', '2', '4 to 9', '-2', FALSE, 4
UNION ALL
SELECT 205, 'Portfolio Size', '3', '10 to 39', '-5', FALSE, 4
UNION ALL
SELECT 206, 'Portfolio Size', '4', '40 to 69', '-7', FALSE, 4
UNION ALL
SELECT 207, 'Portfolio Size', '5', '70 to 99', '-9', FALSE, 4
UNION ALL
SELECT 208, 'Portfolio Size', '6', '100+', '-12', FALSE, 4
UNION ALL
SELECT 209, 'Limit of Liability', '1', 'Less than or equal to 249,999', '0', FALSE, 4
UNION ALL
SELECT 210, 'Limit of Liability', '2', 'Between 250,000 and 999,999; inclusive of 250,000', '2.5', FALSE, 4
UNION ALL
SELECT 211, 'Limit of Liability', '3', 'Between 1,000,000 and 4,999,999; inclusive of 1,000,000', '5', FALSE, 4
UNION ALL
SELECT 212, 'Limit of Liability', '4', '5,000,000 or greater; inclusive of 5,000,000', '7.5', FALSE, 4
UNION ALL
SELECT 213, 'Turnaround Time', '1', '1 day', 'RFP', FALSE, 4
UNION ALL
SELECT 214, 'Turnaround Time', '2', '2 days', 'RFP', FALSE, 4
UNION ALL
SELECT 215, 'Turnaround Time', '3', '3 days', 'RFP', FALSE, 4
UNION ALL
SELECT 216, 'Turnaround Time', '4', '4 days', 'RFP', FALSE, 4
UNION ALL
SELECT 217, 'Turnaround Time', '5', '5 days', 'RFP', FALSE, 4
UNION ALL
SELECT 218, 'Turnaround Time', '6', '6 days', 'RFP', FALSE, 4
UNION ALL
SELECT 219, 'Turnaround Time', '7', '7 days', 'RFP', FALSE, 4
UNION ALL
SELECT 220, 'Turnaround Time', '8', '8 days', 'RFP', FALSE, 4
UNION ALL
SELECT 221, 'Turnaround Time', '9', '9 days', 'RFP', FALSE, 4
UNION ALL
SELECT 222, 'Turnaround Time', '10', '10 days', '20', FALSE, 4
UNION ALL
SELECT 223, 'Turnaround Time', '11', '11 days', '10', FALSE, 4
UNION ALL
SELECT 224, 'Turnaround Time', '12', '12 days', '10', FALSE, 4
UNION ALL
SELECT 225, 'Turnaround Time', '13', '13 days', '0', FALSE, 4
UNION ALL
SELECT 226, 'Turnaround Time', '14', '14 days', '0', FALSE, 4
UNION ALL
SELECT 227, 'Turnaround Time', '15', '15 days', '0', FALSE, 4
UNION ALL
SELECT 228, 'Turnaround Time', '16', '16 days', '0', FALSE, 4
UNION ALL
SELECT 229, 'Turnaround Time', '17', '17 days', '-5', FALSE, 4
UNION ALL
SELECT 230, 'Turnaround Time', '18', '18 days', '-5', FALSE, 4
UNION ALL
SELECT 231, 'Turnaround Time', '19', '19 days', '-5', FALSE, 4
UNION ALL
SELECT 232, 'Turnaround Time', '20', '20 days', '-5', FALSE, 4
UNION ALL
SELECT 233, 'Size', '1', 'XS: Applied to certain Primary Property Types', '-10', FALSE, 4
UNION ALL
SELECT 234, 'Size', '2', 'S: Building SF <= 50,000', '0', FALSE, 4
UNION ALL
SELECT 235, 'Size', '3', 'M: Building SF <= 100,000', '5', FALSE, 4
UNION ALL
SELECT 236, 'Size', '4', 'L:Building SF <= 250,000', '7', FALSE, 4
UNION ALL
SELECT 237, 'Size', '5', 'XL:Building SF <= 500,000', 'RFP', FALSE, 4
UNION ALL
SELECT 238, 'Size', '6', '2XL:Building SF <= 1,000,000', 'RFP', FALSE, 4
UNION ALL
SELECT 239, 'Size', '7', '3XL: Building SF > 1,000,000', 'RFP', FALSE, 4
UNION ALL
SELECT 240, 'Unit Inspection', '1', '1 to 20 units', '0', FALSE, 4
UNION ALL
SELECT 241, 'Unit Inspection', '2', '21 to 40 units', '10', FALSE, 4
UNION ALL
SELECT 242, 'Unit Inspection', '3', '41 to 60 units', '20', FALSE, 4
UNION ALL
SELECT 243, 'Unit Inspection', '4', '61 to 80 units', 'RFP', FALSE, 4
UNION ALL
SELECT 244, 'Unit Inspection', '5', '81 to 100 units', 'RFP', FALSE, 4
UNION ALL
SELECT 245, '# of Buildings 1', '1', '1 to 8 bldgs', '0', FALSE, 4
UNION ALL
SELECT 246, '# of Buildings 1', '2', '9 to 14 bldgs', '10', FALSE, 4
UNION ALL
SELECT 247, '# of Buildings 1', '3', '15 to 20 bldgs', 'RFP', FALSE, 4
UNION ALL
SELECT 248, '# of Buildings 1', '4', '20+ bldgs', 'RFP', FALSE, 4
UNION ALL
SELECT 249, '# of Buildings 2', '1', '1', '0', FALSE, 4
UNION ALL
SELECT 250, '# of Buildings 2', '2', '2 bldgs', '2', FALSE, 4
UNION ALL
SELECT 251, '# of Buildings 2', '3', '3 to 5 bldgs', '4', FALSE, 4
UNION ALL
SELECT 252, '# of Buildings 2', '4', '6 to 9 bldgs', '6', FALSE, 4
UNION ALL
SELECT 253, '# of Buildings 2', '5', '10+ bldgs', 'RFP', FALSE, 4
UNION ALL
SELECT 254, '# of Stories', '1', '0 to 10 stories', '0', FALSE, 4
UNION ALL
SELECT 255, '# of Stories', '2', '11 to 15 stories', '4', FALSE, 4
UNION ALL
SELECT 256, '# of Stories', '3', '16+ stories', 'RFP', FALSE, 4
UNION ALL
SELECT 257, 'Time Period', '1', 'Adjust depending on Partner''s busy level', '15', FALSE, 4

