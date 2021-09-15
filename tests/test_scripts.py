from scripts import archive_datasets

def test_rename_archive_datasets_filename():
    """
    Tests the transformations to be applied by the renaming function
    to make compliant with NCEI archival filenames
    """
    # valid filename
    filename = "test_123-20210913T000030.ncCF.nc3.nc"
    assert (archive_datasets.rename_archive_filename(filename) ==
            filename)
    # excluding seconds is also valid
    filename = "test_123-20210913T0000.ncCF.nc3.nc"
    assert (archive_datasets.rename_archive_filename(filename) ==
            filename)
    # transform to remove hyphens in glider identifier
    filename = "test-123-20210913T000030.ncCF.nc3.nc"
    assert (archive_datasets.rename_archive_filename(filename) ==
            "test_123-20210913T000030.ncCF.nc3.nc")
    # transform to remove Z in datetime string
    filename = "test-123-20210913T000030Z.ncCF.nc3.nc"
    assert (archive_datasets.rename_archive_filename(filename) ==
            "test_123-20210913T000030.ncCF.nc3.nc")
    # -delayed should be transformed to _delayed and moved to glider identifier
    filename = "test-123-20210913T000030-delayed.ncCF.nc3.nc"
    assert (archive_datasets.rename_archive_filename(filename) ==
            "test_123_delayed-20210913T000030.ncCF.nc3.nc")
    # combination of delayed and Z
    filename = "test-123-20210913T000030Z-delayed.ncCF.nc3.nc"
    assert (archive_datasets.rename_archive_filename(filename) ==
            "test_123_delayed-20210913T000030.ncCF.nc3.nc")
    # Invalid characters in glider filename.  Should warn in logs but not
    # attempt to change name (yet)
    filename = "test-123#1-20210913T000030Z-delayed.ncCF.nc3.nc"
    assert (archive_datasets.rename_archive_filename(filename) ==
            filename)
