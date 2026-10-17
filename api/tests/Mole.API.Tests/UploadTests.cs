using System.Net;

namespace Mole.API.Tests;

/// <summary>POST /Init with a file: one PDB or mmCIF structure, optionally gzipped, at most 30 MB also when unpacked</summary>
public class UploadTests
{
    private const int MB = 1024 * 1024;

    [Fact]
    public async Task Plain_upload_is_loaded()
    {
        using var api = new Api();
        var id = await api.LoadAsync();
        var molecule = await api.Client.GetAsync($"/Data/{id}?format=molecule");
        Assert.Equal("1tqn.cif", molecule.Content.Headers.ContentDisposition?.FileName);
        Assert.Equal(Api.Structure, await molecule.Content.ReadAsByteArrayAsync());
    }

    [Fact]
    public async Task Gzipped_upload_is_stored_unpacked()
    {
        using var api = new Api();
        var (status, body) = await api.UploadAsync("1tqn.cif.gz", Api.Gzip(Api.Structure));
        Assert.Equal(HttpStatusCode.OK, status);
        var id = (string)body["ComputationId"]!;
        Assert.Equal("Initialized", (string)(await api.WaitAsync(id))["Status"]!);

        var molecule = await api.Client.GetAsync($"/Data/{id}?format=molecule");
        Assert.Equal("1tqn.cif", molecule.Content.Headers.ContentDisposition?.FileName);
        Assert.Equal(Api.Structure, await molecule.Content.ReadAsByteArrayAsync());
    }

    [Fact]
    public async Task Exactly_30_MB_is_accepted()
    {
        using var api = new Api();
        var (status, body) = await api.UploadAsync("large.pdb", new byte[30 * MB]);
        Assert.Equal(HttpStatusCode.OK, status);
        await api.WaitAsync((string)body["ComputationId"]!);
    }

    public static TheoryData<string, byte[], HttpStatusCode, string> Refused()
    {
        var gzip = Api.Gzip(Api.Structure);
        return new()
        {
            { "empty.pdb", [], HttpStatusCode.BadRequest, "No structure file" },
            { "1tqn.pdb1", Api.Structure, HttpStatusCode.BadRequest, "Unsupported file type" },
            { "1tqn.txt", Api.Structure, HttpStatusCode.BadRequest, "Unsupported file type" },
            { "large.pdb", new byte[30 * MB + 1], HttpStatusCode.RequestEntityTooLarge, "at most 30 MB can be uploaded" },
            { "large.pdb.gz", Api.Gzip(new byte[30 * MB + 1]), HttpStatusCode.RequestEntityTooLarge, "unpacks to more than 30 MB" },
            { "broken.cif.gz", "xxxxxxxx"u8.ToArray(), HttpStatusCode.BadRequest, "not a valid gzip file" },
            { "one_byte.cif.gz", "x"u8.ToArray(), HttpStatusCode.BadRequest, "not a valid gzip file" },
            { "header_only.cif.gz", gzip[..5], HttpStatusCode.BadRequest, "not a valid gzip file" },
            { "truncated.cif.gz", gzip[..(gzip.Length / 2)], HttpStatusCode.BadRequest, "it is incomplete" }, // GZipStream reads it without an error
        };
    }

    [Theory]
    [MemberData(nameof(Refused), DisableDiscoveryEnumeration = true)] // the 30 MB contents stay out of the test names
    public async Task Refused_upload_creates_no_computation(string fileName, byte[] content, HttpStatusCode expected, string message)
    {
        using var api = new Api();
        var (status, body) = await api.UploadAsync(fileName, content);
        Assert.Equal(expected, status);
        Assert.Equal("Error", (string)body["Status"]!);
        Assert.Contains(message, (string)body["ErrorMsg"]!);
        Assert.DoesNotContain(Directory.GetFileSystemEntries(api.WorkingDirectory), e => Directory.Exists(e) || Path.GetFileName(e).StartsWith(".upload-"));
    }

    [Fact]
    public async Task Upload_without_a_file_is_refused()
    {
        using var api = new Api();
        using var form = new MultipartFormDataContent { { new StringContent("x"), "other" } };
        Assert.Equal(HttpStatusCode.BadRequest, (await api.Client.PostAsync("/Init", form)).StatusCode);
    }
}
