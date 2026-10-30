"""Preview-only deterministic sorting, no global catalog loads."""

def sorted_preview(rows, column='id', descending=True):
    if column=='id':return rows
    idx={'path':0,'type':1,'size':2}.get(column,0)
    if column=='size':
        # Preview display sizes contain units; sort using decimal magnitudes.
        scales={'B':1,'KiB':1024,'MiB':1024**2,'GiB':1024**3,'TiB':1024**4,
                'KB':1000,'MB':1000**2,'GB':1000**3}
        def size(row):
            try:
                number,unit=str(row[2]).split()[:2]
                return float(number)*scales.get(unit,1)
            except (ValueError,IndexError):return -1
        return sorted(rows,key=lambda r:(size(r),str(r[0]).casefold()),reverse=descending)
    return sorted(rows,key=lambda r:(str(r[idx]).casefold(),str(r[0]).casefold()),reverse=descending)
