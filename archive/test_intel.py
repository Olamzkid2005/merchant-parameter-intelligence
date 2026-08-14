from merchant_intel import *

tool = MerchantIntel(WORKBOOK)
try:
    tool.initialize()
    tool.run_alias_engine()
    tool.run_learning_merge()
    
    for query in ['FILM HOUSE', 'SPAR', 'BEACON HEALTH', 'RUBELS']:
        results = tool.search(query)
        top = results['scored_records'][:5]
        print(f'\n=== Top results for "{query}" ===')
        for score_total, scores, rec in top:
            name = rec["merchant_name"] or "(unnamed)"
            print(f'  [{score_total:3d}%] {name[:50]} | {rec["sheet_name"]} row {rec["row_num"]} | {rec["email"]}')
        
        if results['similar']:
            print(f'  Similar: {[n for n,s in results["similar"][:5]]}')
        if results['duplicates']:
            print(f'  Dup groups: {len(results["duplicates"])}')
        tool.learn_from_results(results)
        print()

    results = tool.search('FILM HOUSE IMAX')
    tool.generate_report(results)
    tool.learn_from_results(results)
    print('\nReport generated successfully')
    
finally:
    tool.close()
