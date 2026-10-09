# PO

## Any customer

## Alfamart (chain 1100002312, 1100002314, 1100002311, 1100002310)
- purchase_order_no: printed after "FPP Number". [label · FPP Number]
- vendor_code: the code in square brackets after the supplier name. [label · Supplier Name]
- vendor_name: printed after "Supplier Name". [label · Supplier Name]
- ppn: printed after "TOTAL VAT INPUT". [label · TOTAL VAT INPUT]
- total: printed after "TOTAL INVOICE". [label · TOTAL INVOICE]
- lines.customer_item_code: the PLU column. [column · PLU]
- lines.description: the PRODUCT NAME column. [column · PRODUCT NAME]
- lines.qty: the Q_Crt column, the number of cartons. [column · Q_Crt]
- lines.satuan: the CONT(C) column, the pieces in one carton (e.g. 72). [column · CONT(C)]

## Alfamidi (chain 1100002309, 1100002308)
- purchase_order_no: printed after "FPP Number". [label · FPP Number]
- vendor_code: the code in square brackets after the supplier name. [label · Supplier Name]
- vendor_name: printed after "Supplier Name". [label · Supplier Name]
- ppn: printed after "TOTAL VAT INPUT". [label · TOTAL VAT INPUT]
- total: printed after "TOTAL INVOICE". [label · TOTAL INVOICE]
- lines.customer_item_code: the PLU column. [column · PLU]
- lines.description: the PRODUCT NAME column. [column · PRODUCT NAME]
- lines.qty: the Q_Crt column, the number of cartons. [column · Q_Crt]
- lines.satuan: the CONT(C) column, the pieces in one carton (e.g. 24). [column · CONT(C)]

## Hari Hari (chain 1100002542)
- purchase_order_no: printed after "No PO". [label · No PO]
- vendor_code: printed after "No Supplier". [label · No Supplier]
- vendor_name: printed after "Nama Supplier". [label · Nama Supplier]
- ppn: printed after "PPN 11%". [label · PPN 11%]
- total: printed after "TOTAL". [label · TOTAL]
- lines.customer_item_code: the SKU column. [column · SKU]
- lines.description: the Nama Barang column. [column · Nama Barang]
- lines.qty: the quantity ordered with its unit (e.g. "6 BX"), never the case size beside it (e.g. "12 PC"). [column · 6 BX]
- lines.uom: the unit printed with the quantity ordered (e.g. BX). [column · BX]
- lines.satuan: the case size, the pieces in one case (e.g. 12 in "12 PC"). [column · Qty/Case]

## Indomaret (chain 1100002480)
- purchase_order_no: printed after "NO.PO". [label · NO.PO]
- vendor_code: the number in brackets after SAMB's name, before "(PKP)" (e.g. 18844). Not the number after "MERCHANDISING INDOMARCO GROUP". [label · (18844)]
- vendor_name: SAMB's name, printed with no label after "MERCHANDISING INDOMARCO GROUP (…)".
- ppn: no PPN total is printed (the P.P.N column is per row); leave it empty. [not_printed]
- total: printed after "TOTAL SELURUHNYA". [label · TOTAL SELURUHNYA]
- lines.customer_item_code: the PLU on the row's second line, before the quantity. [column · PLU]
- lines.description: the Merk & Nama Barang column. [column · Merk & Nama Barang]
- lines.qty: the QTY column, on the row's second line. [column · QTY]
- lines.uom: the word in the SAT column (e.g. CTN in "CTN/72"). [column · SAT]
- lines.satuan: the number in the SAT column, the pieces in one carton (e.g. 72 in "CTN/72"). [column · SAT]

## FoodHall (chain 1100002550)
- purchase_order_no: the number under the title "Purchase Order", next to the barcode. [label · Purchase Order]
- vendor_code: the number printed under SAMB's name at the top left (e.g. 13568). [label · SAMB's name]
- vendor_name: SAMB's name at the top left, with no label.
- ppn: printed after "PPN". [label · PPN]
- total: printed after "Total Include Tax". [label · Total Include Tax]
- lines.customer_item_code: the Article SKU column. [column · Article SKU]
- lines.description: the Description column. [column · Description]
- lines.qty: the Qty column. [column · Qty]
- lines.uom: the UoM column (e.g. EA). [column · UoM]

## Naga Swalayan (chain 1100002509)
- purchase_order_no: printed after "No. PO" (e.g. 10300626-2000118). [label · No. PO]
- vendor_name: printed after "Kepada". [label · Kepada]
- ppn: printed after "PPN" in the totals at the bottom. [label · PPN]
- total: printed after "T o t a l :" at the bottom right, not Subtotal and not "Ttl Rp/Hal". [label · Total]
- lines.customer_item_code: the code on the row's second line, before the barcode (e.g. 00613087). [column · PLU]
- lines.description: the item name on the row's first line. [column · Nama Barang]
- lines.qty: the Qty column. [column · Qty]
- lines.uom: the SAT column (e.g. CRT). [column · SAT]
- lines.satuan: the Isi column, the pieces in one carton (e.g. 24.00). [column · Isi]

## Farmers Market (chain 1100002547)
- purchase_order_no: the PO number at the top, printed twice near SAMB's address (e.g. 3014025840). [label · top]
- vendor_code: SAMB's code is not printed on this PO; leave it empty. [not_printed]
- vendor_name: printed after "SUPPLIER Name". [label · SUPPLIER Name]
- ppn: printed after "VAT Total". [label · VAT Total]
- total: printed after "Grand", the last total. [label · Grand]
- lines.customer_item_code: the code line of the Product Code & Name column. [column · Product Code]
- lines.description: the name line of the Product Code & Name column. [column · Product Name]
- lines.qty: the Qty Order column. [column · Qty Order]
- lines.uom: the UOM column. [column · UOM]

## AEON (chain 1100002424)
- purchase_order_no: printed after "PO No". [label · PO No]
- vendor_code: the supplier number (e.g. 0000000398). [label · Supplier No]
- vendor_name: printed after "Supplier Name". [label · Supplier Name]
- ppn: printed after "VAT AMOUNT". [label · VAT AMOUNT]
- total: printed after "NET TOTAL WITH VAT". [label · NET TOTAL WITH VAT]
- lines.customer_item_code: the ITEM NO column. [column · ITEM NO]
- lines.description: the ITEM DESCRIPTION column. [column · ITEM DESCRIPTION]
- lines.qty: the ORDER QTY column, never QTY/CASE SIZE (that is the pieces in one case). [column · ORDER QTY]
- lines.uom: the word in the UOM column (e.g. CARTON). [column · UOM]
- lines.satuan: the QTY/CASE SIZE column, the pieces in one case (e.g. 20.00). [column · QTY/CASE SIZE]

## Indogrosir (chain 1100002481)
- purchase_order_no: printed after "NO.PO". [label · NO.PO]
- vendor_code: the number in brackets after SAMB's name, before "(PKP)" (e.g. 18844). Not the number after "MERCHANDISING INDOMARCO GROUP". [label · (18844)]
- vendor_name: SAMB's name, printed with no label after "MERCHANDISING INDOMARCO GROUP (…)".
- ppn: no PPN total is printed (the P.P.N column is per row); leave it empty. [not_printed]
- total: printed after "TOTAL SELURUHNYA". [label · TOTAL SELURUHNYA]
- lines.customer_item_code: the PLU on the row's second line, before the quantity. [column · PLU]
- lines.description: the Merk & Nama Barang column. [column · Merk & Nama Barang]
- lines.qty: the QTY column, on the row's second line. [column · QTY]
- lines.uom: the word in the SAT column (e.g. CTN in "CTN/10"). [column · SAT]
- lines.satuan: the number in the SAT column, the pieces in one carton (e.g. 10 in "CTN/10"). [column · SAT]

## Tip Top (chain 1100002553)
- purchase_order_no: printed after "No. PO". [label · No. PO]
- vendor_code: the number in brackets after SAMB's name (e.g. 00146). [label · ( 00146 )]
- vendor_name: printed after "Nama" in the supplier box. [label · Nama]
- ppn: printed after "VAT / PPN". [label · VAT / PPN]
- total: printed after "Total Purchase / JUMLAH PEMBELIAN", not "Gross Amount/Harga Sebelum Potongan". [label · Total Purchase / JUMLAH PEMBELIAN]
- lines.customer_item_code: the SKU column. [column · SKU]
- lines.description: the Nama Barang column. [column · Nama Barang]
- lines.qty: the Jumlah Order column. [column · Jumlah Order]
- lines.uom: the word in the Satuan column (e.g. CTN in "CTN12"). [column · Satuan]
- lines.satuan: the number in the Satuan column, the pieces in one carton (e.g. 12 in "CTN12"). [column · Satuan]

## Hypermart (chain 1100002499)
- purchase_order_no: printed after "PO#". [label · PO#]
- vendor_code: printed after "VENDOR". [label · VENDOR]
- vendor_name: printed after "VENDOR NAME". [label · VENDOR NAME]
- ppn: printed after "PPN" in the totals. [label · PPN]
- total: printed after "GRAND TOTAL". [label · GRAND TOTAL]
- lines.customer_item_code: the SKU column, not UPC. [column · SKU]
- lines.description: the KETERANGAN column. [column · KETERANGAN]
- lines.qty: the Total Unit column, the number of cases. [column · Total Unit]
- lines.satuan: the KWT/CASE column, the pieces in one case (e.g. 36). [column · KWT/CASE]

## Ramayana (chain 1100002531)
- purchase_order_no: the number under the title PURCHASE ORDER, written with dots (e.g. 9609.0600.2415). [label · PURCHASE ORDER]
- vendor_code: printed after "No Supplier". [label · No Supplier]
- vendor_name: SAMB's name printed under "No Supplier", with no label.
- ppn: printed after "PPN" in the totals box at the bottom. [label · PPN]
- total: printed after "Total Extended", the last line of the totals box. [label · Total Extended]
- lines.customer_item_code: the No.Sku column. [column · No.Sku]
- lines.description: the Nama Barang column. [column · Nama Barang]
- lines.qty: the Qty column. [column · Qty]
- lines.uom: the unit printed under the quantity (e.g. PCS). [column · UOM]
- lines.satuan: not printed; Ramayana counts pieces. Leave it empty. [not_printed]

## Lotte Grosir (chain 1100002495)
- purchase_order_no: the number under the barcode, also printed after "Order No". [label · Order No]
- vendor_code: printed after "Supplier Id". [label · Supplier Id]
- vendor_name: printed after "Supplier :". [label · Supplier]
- ppn: no PPN is printed on this PO; leave it empty. [not_printed]
- total: the total at the bottom right, marked "* Exclude PPN" (before tax). [label · Exclude PPN]
- lines.customer_item_code: the Product Code, the number above the barcode. [column · Product Code]
- lines.description: the Product Name column. [column · Product Name]
- lines.qty: the Qty column. [column · Qty]
- lines.satuan: the number in the UOM / Unit column, the pieces in one unit (e.g. 24 in "24 / EA"). [column · UOM / Unit]

## Lotte Mart (chain 1100002494)
- purchase_order_no: the number under the barcode, also printed after "Order No". [label · Order No]
- vendor_code: printed after "Supplier Id". [label · Supplier Id]
- vendor_name: printed after "Supplier :". [label · Supplier]
- ppn: no PPN is printed on this PO; leave it empty. [not_printed]
- total: the total at the bottom right, marked "* Exclude PPN" (before tax). [label · Exclude PPN]
- lines.customer_item_code: the Product Code, the number above the barcode. [column · Product Code]
- lines.description: the Product Name column. [column · Product Name]
- lines.qty: the Qty column. [column · Qty]
- lines.satuan: the number in the UOM / Unit column, the pieces in one unit (e.g. 24 in "24 / Box"). [column · UOM / Unit]

## Super Indo (chain 1100002493)
- purchase_order_no: printed after "NO PO" (also after "PO Number" at the very top). [label · NO PO]
- vendor_code: the code in brackets after SAMB's name (e.g. S6321). [label · (S6321)]
- vendor_name: SAMB's name at the top right, with no label.
- ppn: no PPN total is printed (the PPN column is per row); leave it empty. [not_printed]
- total: printed after "TOTAL SELURUHNYA". [label · TOTAL SELURUHNYA]
- lines.customer_item_code: the PLU column. [column · PLU]
- lines.description: the DESKRIPSI BARANG column. [column · DESKRIPSI BARANG]
- lines.qty: the QTY column. [column · QTY]
- lines.uom: the word in brackets under the item name (e.g. CTN in "[CTN/6]"). [column · CTN/6]
- lines.satuan: the number in brackets under the item name, the pieces in one carton (e.g. 6 in "[CTN/6]"). [column · CTN/6]

## GrandLucky (chain 1100002502, 1100002496, 1100002477, 1100002476)
- purchase_order_no: printed after "PO ID". [label · PO ID]
- vendor_code: printed after "Supplier" at the top left, not the "(SAP)" number. [label · Supplier]
- vendor_name: printed after "Supplier Name". [label · Supplier Name]
- ppn: printed after "VAT". [label · VAT]
- total: printed after "Total PO Amount". [label · Total PO Amount]
- lines.customer_item_code: the SKU, the number above the barcode. [column · SKU]
- lines.description: the Product Name column. [column · Product Name]
- lines.qty: the Req Qty EA/KG column, in pieces. [column · Req Qty EA/KG]
- lines.uom: the quantity counts pieces; the UOM column (e.g. CAR) names the pack, not what the quantity counts. Leave it empty. [not_printed]
- lines.satuan: the Qty Base UOM column, the pieces in one carton (e.g. 24). [column · Qty Base UOM]

## Yogya (chain 1100002428)
- purchase_order_no: the ORDER NO column of the header table. [label · ORDER NO]
- vendor_code: the SUPPLIER CODE column of the header table. [label · SUPPLIER CODE]
- vendor_name: the name under ORDERED FROM. [label · ORDERED FROM]
- ppn: no PPN amount is printed on this PO; leave it empty. [not_printed]
- total: printed after "After PPN". [label · After PPN]
- lines.customer_item_code: the External Code column, not Article Code. [column · External Code]
- lines.description: the Article Description column. [column · Article Description]
- lines.qty: the number of cartons in the In CTN column: the number before the colon (e.g. 1 in "1: 0"). [column · In CTN]
- lines.satuan: the pieces in one carton: Qty Order divided by the cartons in In CTN (e.g. 12.00 and "1: 0" give 12). [column · Qty Order]

## Hero (chain 1100002473, 1100002447)
- purchase_order_no: printed after "NO PO". [label · NO PO]
- vendor_code: the code after "Kepada YTH:", before SAMB's name (e.g. S1006H). [label · Kepada YTH]
- vendor_name: SAMB's name after "Kepada YTH:" and the code. [label · Kepada YTH]
- ppn: printed after "PPN (%)". [label · PPN (%)]
- total: printed after "TOTAL NETTO", not TOTAL PURCHASE. [label · TOTAL NETTO]
- lines.customer_item_code: the PLU on top in the PLU (BAR) column, not the barcode in brackets. [column · PLU (BAR)]
- lines.description: the NAMA BARANG line. [column · NAMA BARANG]
- lines.qty: the JUMLAH PESANAN column (e.g. "29 CT"). [column · JUMLAH PESANAN]
- lines.uom: the unit printed with JUMLAH PESANAN (e.g. CT). [column · JUMLAH PESANAN]
- lines.satuan: the ISI KARTON column: the number after the x (e.g. 6 in "1x6"). Not JUMLAH SATUAN. [column · ISI KARTON]

## Diamond (chain 1100002483)
- purchase_order_no: printed after "No." under PURCHASE ORDER (e.g. PO3120260040525). [label · No.]
- vendor_code: the Kode column of the supplier box (e.g. SH479). [label · Kode]
- vendor_name: the Nama Supplier column of the supplier box. [label · Nama Supplier]
- ppn: printed after "PPN (EXCLUDE)". [label · PPN (EXCLUDE)]
- total: printed after "TOTAL PEMBELIAN". [label · TOTAL PEMBELIAN]
- lines.customer_item_code: the PLU column, not Item ID. [column · PLU]
- lines.description: the Nama Barang column. [column · Nama Barang]
- lines.qty: the Total Order column (e.g. "2 CTN"). [column · Total Order]
- lines.uom: the unit printed in Total Order (e.g. CTN). [column · Total Order]
- lines.satuan: the Isi column, the pieces in one carton (e.g. 12). [column · Isi]

## FamilyMart (chain 1100002458)
- purchase_order_no: printed after "PO No". [label · PO No]
- vendor_code: printed after "Supplier" (e.g. 11899). [label · Supplier]
- vendor_name: the name printed after the supplier code. [label · Supplier]
- ppn: printed after "TAX". [label · TAX]
- total: printed after "GRAND TOTAL". [label · GRAND TOTAL]
- lines.customer_item_code: the code before the barcode, on the row's first line. [column · Description]
- lines.description: the item name on the row's second line. [column · Description]
- lines.qty: the Qty column. [column · Qty]
- lines.uom: the UoM column (e.g. PCS). [column · UoM]
- lines.satuan: not printed; FamilyMart counts pieces. Leave it empty. [not_printed]

## Boots (chain 1100002518)
- purchase_order_no: the number under the title "Purchase Order", next to the barcode. [label · Purchase Order]
- vendor_code: the number printed under SAMB's name at the top left (e.g. 8328). [label · SAMB's name]
- vendor_name: SAMB's name at the top left, with no label.
- ppn: printed after "PPN". [label · PPN]
- total: printed after "Total Include Tax". [label · Total Include Tax]
- lines.customer_item_code: the Article SKU, the code above the barcode. [column · Article SKU]
- lines.description: the Description column. [column · Description]
- lines.qty: the Qty column. [column · Qty]
- lines.uom: the UoM column (e.g. EA). [column · UoM]
- lines.satuan: not printed; Boots counts pieces. Leave it empty. [not_printed]

## Megah Daya Inti Harapan (chain 1100002500)
- purchase_order_no: printed after "NO.PO". [label · NO.PO]
- vendor_code: the number in brackets after SAMB's name, before "(PKP)" (e.g. 18844). Not the number after "MERCHANDISING INDOMARCO GROUP". [label · (18844)]
- vendor_name: SAMB's name, printed with no label after "MERCHANDISING INDOMARCO GROUP (…)".
- ppn: no PPN total is printed (the P.P.N column is per row); leave it empty. [not_printed]
- total: printed after "TOTAL SELURUHNYA". [label · TOTAL SELURUHNYA]
- lines.customer_item_code: the PLU on the row's second line, before the quantity (e.g. 1588560). [column · PLU]
- lines.description: the Merk & Nama Barang column. [column · Merk & Nama Barang]
- lines.qty: the QTY column, on the row's second line. [column · QTY]
- lines.uom: the word in the SAT column (e.g. CTN in "CTN/120"). [column · SAT]
- lines.satuan: the number in the SAT column, the pieces in one carton (e.g. 120 in "CTN/120"). [column · SAT]

## Total Buah Segar (chain 1100002337, 1100002555, 1100002557, 1100002561, 1100002599, 1100002600, 1100002601, 1100002602, 1100002603, 1100002604, 1100002605, 1100002607, 1100002608, 1100002609, 1100002612, 1100002613, 1100002614, 1100002615, 1100002616, 1100002617, 1100002618)
- purchase_order_no: printed after "No Pemesanan" (e.g. PO/0011/0926/1475). [label · No Pemesanan]
- vendor_code: printed after "Kode Supplier". [label · Kode Supplier]
- vendor_name: printed after "Supplier". [label · Supplier]
- ppn: printed after "VAT". [label · VAT]
- total: printed after "Net Total". [label · Net Total]
- lines.customer_item_code: the ProdCode column. [column · ProdCode]
- lines.description: the Produk column. [column · Produk]
- lines.qty: the Qty Up column (e.g. "1 DUS"). [column · Qty Up]
- lines.uom: the unit printed in Qty Up (e.g. DUS). [column · Qty Up]
- lines.satuan: the number in the Qty Down column, the pieces in one pack (e.g. 24 in "24 PCS"). [column · Qty Down]

## Watsons (chain 1100002452)
- purchase_order_no: printed after "PO No.". [label · PO No.]
- vendor_code: printed after "Vendor Code". [label · Vendor Code]
- vendor_name: printed after "Vendor Name". [label · Vendor Name]
- ppn: the GST total at the bottom. [label · GST]
- total: the TOTAL COST WITH GST total at the bottom. [label · TOTAL COST WITH GST]
- lines.customer_item_code: the PRDT CODE column. [column · PRDT CODE]
- lines.description: the DESCRIPTION column. [column · DESCRIPTION]
- lines.qty: the ORDER QTY (PCS) column, in pieces. [column · ORDER QTY (PCS)]
