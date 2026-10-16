using Microsoft.AspNetCore.Mvc;
using Mole.API.Models;

namespace Mole.API.Controllers;

/// <summary>
/// JSON responses: a computation report, with an HTTP error status when the request failed (ErrorMsg says why).
/// A report about a computation that failed is not a failed request, it is 200.
/// </summary>
internal static class Reports
{
    public static ContentResult Json(ComputationReport report, int statusCode = StatusCodes.Status200OK) =>
        new() { Content = report.ToJson(), ContentType = "application/json", StatusCode = statusCode };

    public static ContentResult Error(string computationId, int statusCode, string message, int submitId = 0) =>
        Json(new ComputationReport(computationId, submitId, ComputationStatus.Error, message), statusCode);

    public static ContentResult NotFound(string computationId) =>
        Json(Computation.NotExists(computationId), StatusCodes.Status404NotFound);

    public static ContentResult Gone(string computationId) =>
        Error(computationId, StatusCodes.Status410Gone, "The computation has been deleted.");
}
